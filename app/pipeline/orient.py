"""Step 2: classify each sheet's layout and, where possible, extract a tidy
long-format table.

Orientations
------------
* ``tidy``    — header row on top, one record per row below it.
* ``matrix``  — cross-tab: labels/metrics down the rows, a date axis running
                across the columns. Unpivoted to ``(label..., period, value)``.
* ``form``    — sparse key/value report (few cells per row).
* ``unknown`` — we cannot tell; decline honestly.

Everything here is heuristic and value-based. No sheet names, no file-specific
special cases.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .celltypes import cell_kind, is_blank, BLANK, DATE, NUMBER, TEXT
from .checks import run_checks
from .coerce import coerce_value
from .jsonsafe import jsonify
from .metrics import ColumnAcc, table_summary
from .profile import guess_header_row

# Tunables for the heuristics (documented at point of use).
MIN_DATE_RUN = 3          # a period axis needs at least this many date cells
DATE_AXIS_FRAC = 0.6      # ... and this fraction of the axis's filled cells
INTERIOR_NUMERIC = 0.5    # matrix interior must be at least this numeric
SCAN_ROWS = 15            # how deep to look for a period/header row

# Plausible calendar years for a year-number period axis (RECAP-style sheets
# where the columns are 2025, 2026, ... instead of real dates).
YEAR_MIN, YEAR_MAX = 1990, 2100
# "2019-2021" / "2025/2026" style year-range labels also count as periods.
_YEAR_RANGE_RE = re.compile(
    r"^\s*((?:19|20)\d{2})\s*(?:[-/à–]\s*(?:19|20)\d{2})?\s*$")
# A year axis must sit above almost all the numeric columns; when years are
# sparse *group bands* over multi-column blocks, the sheet is not a plain
# cross-tab and unpivoting on the year cells alone would drop data.
YEAR_AXIS_COVERAGE = 0.7


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def _kinds(df: pd.DataFrame) -> List[List[str]]:
    return [[cell_kind(df.iat[i, j]) for j in range(df.shape[1])]
            for i in range(df.shape[0])]


def bounding_box(kinds: List[List[str]]) -> Optional[Tuple[int, int, int, int]]:
    """Smallest (r0, r1, c0, c1) inclusive box covering all non-blank cells."""
    rows = [i for i, row in enumerate(kinds) if any(k != BLANK for k in row)]
    if not rows:
        return None
    cols = [j for j in range(len(kinds[0]))
            if any(kinds[i][j] != BLANK for i in range(len(kinds)))]
    if not cols:
        return None
    return rows[0], rows[-1], cols[0], cols[-1]


def _counts(kinds_row: List[str], c0: int, c1: int) -> Dict[str, int]:
    out = {BLANK: 0, NUMBER: 0, DATE: 0, TEXT: 0}
    for j in range(c0, c1 + 1):
        out[kinds_row[j]] += 1
    return out


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def _find_date_row(kinds, r0, r1, c0, c1) -> Optional[Tuple[int, List[int], float]]:
    """Best horizontal date axis: (row_index, period_col_indices, strength)."""
    best = None
    for i in range(r0, min(r1 + 1, r0 + SCAN_ROWS)):
        cnt = _counts(kinds[i], c0, c1)
        filled = cnt[NUMBER] + cnt[DATE] + cnt[TEXT]
        if cnt[DATE] < MIN_DATE_RUN or filled == 0:
            continue
        frac = cnt[DATE] / filled
        if frac < DATE_AXIS_FRAC:
            continue
        period_cols = [j for j in range(c0, c1 + 1) if kinds[i][j] == DATE]
        strength = frac * min(1.0, cnt[DATE] / 5.0)
        if best is None or cnt[DATE] > len(best[1]):
            best = (i, period_cols, strength)
    return best


def _year_value(v) -> Optional[int]:
    """The year a cell represents, or ``None``.

    Accepts integer cells in [YEAR_MIN, YEAR_MAX] and text like ``2025`` or
    ``2019-2021`` (a range labels the period by its first year).
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if float(v).is_integer() and YEAR_MIN <= v <= YEAR_MAX:
            return int(v)
        return None
    if isinstance(v, str):
        m = _YEAR_RANGE_RE.match(v)
        if m:
            y = int(m.group(1))
            if YEAR_MIN <= y <= YEAR_MAX:
                return y
    return None


def _find_year_row(df, kinds, r0, r1, c0, c1) -> Optional[Tuple[int, List[int], float]]:
    """Best horizontal *year* axis: (row_index, period_col_indices, strength).

    Stricter than the date axis, because plain numbers are ambiguous: the
    years must be strictly increasing left-to-right. A data row that happens
    to contain a few 4-digit values almost never is.
    """
    best = None
    for i in range(r0, min(r1 + 1, r0 + SCAN_ROWS)):
        cnt = _counts(kinds[i], c0, c1)
        filled = cnt[NUMBER] + cnt[DATE] + cnt[TEXT]
        if filled == 0:
            continue
        year_cols = [j for j in range(c0, c1 + 1)
                     if kinds[i][j] != BLANK and _year_value(df.iat[i, j]) is not None]
        if len(year_cols) < MIN_DATE_RUN:
            continue
        frac = len(year_cols) / filled
        if frac < DATE_AXIS_FRAC:
            continue
        years = [_year_value(df.iat[i, j]) for j in year_cols]
        if any(a >= b for a, b in zip(years, years[1:])):
            continue                      # not strictly increasing -> not an axis
        strength = frac * min(1.0, len(year_cols) / 5.0)
        if best is None or len(year_cols) > len(best[1]):
            best = (i, year_cols, strength)
    return best


def _interior_numeric_frac(kinds, date_row, period_cols, r1) -> float:
    filled = numeric = 0
    for i in range(date_row + 1, r1 + 1):
        for j in period_cols:
            k = kinds[i][j]
            if k == BLANK:
                continue
            filled += 1
            if k == NUMBER:
                numeric += 1
    return numeric / filled if filled else 0.0


def _label_cols(kinds, r0, r1, c0, first_period_col) -> List[int]:
    """Columns left of the period block whose body is mostly text labels."""
    out = []
    for j in range(c0, first_period_col):
        filled = text = 0
        for i in range(r0, r1 + 1):
            k = kinds[i][j]
            if k == BLANK:
                continue
            filled += 1
            if k == TEXT:
                text += 1
        if filled and text / filled >= 0.5:
            out.append(j)
    return out


def _composite_header_rows(df, kinds, r0, r1, c0, c1) -> Optional[List[int]]:
    """Multi-band headers: several sparse rows that only make sense together
    (banner / field names / year bands), none passing the single-row heuristic.

    Scanning from the top: a row joins the stack when all its filled cells are
    labels (text, dates, or year numbers) and there are at least two of them.
    Single-cell text rows (section banners) are skipped, not stacked. The first
    row containing a plain non-year number is data — stop there. Needs >= 2
    stacked layers and data below to count.
    """
    layers: List[int] = []
    limit = min(r1, r0 + SCAN_ROWS)
    for i in range(r0, limit + 1):
        cells = [(j, kinds[i][j]) for j in range(c0, c1 + 1)
                 if kinds[i][j] != BLANK]
        if not cells:
            continue
        if any(k == NUMBER and _year_value(df.iat[i, j]) is None
               for j, k in cells):
            break                        # plain numbers -> the data has begun
        if len(cells) >= 2:
            layers.append(i)
    if len(layers) >= 2 and layers[-1] < r1:
        return layers
    return None


def _form_score(kinds, r0, r1, c0, c1) -> float:
    """Fraction of non-blank rows that carry only 1-2 filled cells (key/value)."""
    sparse = total = 0
    for i in range(r0, r1 + 1):
        cnt = _counts(kinds[i], c0, c1)
        filled = cnt[NUMBER] + cnt[DATE] + cnt[TEXT]
        if filled == 0:
            continue
        total += 1
        if filled <= 2:
            sparse += 1
    return sparse / total if total else 0.0


def classify(df: pd.DataFrame) -> dict:
    """Return orientation + confidence + extraction metadata for one sheet."""
    kinds = _kinds(df)
    box = bounding_box(kinds)
    if box is None:
        return {"orientation": "unknown", "confidence": 0.0, "meta": {},
                "reason": "sheet is empty"}
    r0, r1, c0, c1 = box

    # 1) Matrix: a strong horizontal period axis — real dates, or strictly
    #    increasing year numbers — with a numeric interior and at least one
    #    text label column to the left.
    for axis, found in (("date", _find_date_row(kinds, r0, r1, c0, c1)),
                        ("year", _find_year_row(df, kinds, r0, r1, c0, c1))):
        if found is None:
            continue
        date_row, period_cols, strength = found
        if axis == "year":
            # Numeric body columns from the first period onward that are NOT
            # under a year cell mean the years are group bands, not an axis.
            numeric_body = [
                j for j in range(period_cols[0], c1 + 1)
                if any(kinds[i][j] == NUMBER for i in range(date_row + 1, r1 + 1))
            ]
            covered = len(set(period_cols) & set(numeric_body))
            if numeric_body and covered / len(numeric_body) < YEAR_AXIS_COVERAGE:
                continue
        interior = _interior_numeric_frac(kinds, date_row, period_cols, r1)
        labels = _label_cols(kinds, date_row, r1, c0, period_cols[0])
        if interior >= INTERIOR_NUMERIC and labels:
            conf = round(min(1.0, 0.5 * strength + 0.5 * interior), 3)
            return {
                "orientation": "matrix",
                "confidence": conf,
                "meta": {"date_row": date_row, "period_cols": period_cols,
                         "label_cols": labels, "box": box, "axis": axis},
                "reason": (f"{axis} axis across columns at row {date_row} "
                           f"({len(period_cols)} periods), interior "
                           f"{interior:.0%} numeric"),
            }

    # 2) Form: a sparse key/value report. Checked before tidy because a wide
    #    tidy table has many cells per row (so this won't fire), while a narrow
    #    key/value sheet would otherwise be mislabelled as a 2-column tidy table.
    form = _form_score(kinds, r0, r1, c0, c1)
    if form >= 0.6:
        return {"orientation": "form", "confidence": round(form, 3),
                "meta": {"box": box},
                "reason": f"{form:.0%} of rows are sparse key/value pairs"}

    # 3) Tidy: a text header row with genuine records beneath it.
    header = guess_header_row(df)
    if header is not None and header < r1:
        data_rows = sum(
            any(kinds[i][j] != BLANK for j in range(c0, c1 + 1))
            for i in range(header + 1, r1 + 1)
        )
        if data_rows >= 1:
            hdr_cnt = _counts(kinds[header], c0, c1)
            hdr_filled = hdr_cnt[TEXT] + hdr_cnt[NUMBER] + hdr_cnt[DATE]
            text_frac = hdr_cnt[TEXT] / hdr_filled if hdr_filled else 0.0
            span = min(1.0, data_rows / 3.0)
            conf = round(min(1.0, 0.5 * text_frac + 0.5 * span), 3)
            return {
                "orientation": "tidy",
                "confidence": conf,
                "meta": {"header_row": header, "box": box},
                "reason": f"text header at row {header} with {data_rows} record row(s)",
            }

    # 4) Composite multi-band header: no single row qualified, but a stack of
    #    sparse label rows above the data does.
    layers = _composite_header_rows(df, kinds, r0, r1, c0, c1)
    if layers is not None:
        data_rows = sum(
            any(kinds[i][j] != BLANK for j in range(c0, c1 + 1))
            for i in range(layers[-1] + 1, r1 + 1)
        )
        if data_rows >= 1:
            return {
                "orientation": "tidy",
                "confidence": round(min(1.0, 0.3 + 0.1 * len(layers)
                                        + 0.1 * min(data_rows, 3)), 3),
                "meta": {"header_rows": layers, "box": box},
                "reason": (f"composite multi-band header rows {layers} "
                           f"with {data_rows} record row(s)"),
            }

    # 5) Decline honestly.
    return {"orientation": "unknown", "confidence": 0.0, "meta": {"box": box},
            "reason": "no orientation matched"}


# --------------------------------------------------------------------------- #
# Extraction -> normalized long/tidy table
# --------------------------------------------------------------------------- #
def _unique_names(raw: List[Optional[object]]) -> List[str]:
    names, seen = [], {}
    for j, v in enumerate(raw):
        base = str(v).strip() if not is_blank(v) else f"col_{j}"
        if base == "":
            base = f"col_{j}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        names.append(base if n == 0 else f"{base}.{n}")
    return names


def _ffill_label_cols(df: pd.DataFrame, kinds, cols, r_start, r_end) -> Dict[Tuple[int, int], object]:
    """Forward-fill label columns downward to spread merged-cell values."""
    filled = {}
    for j in cols:
        last = None
        for i in range(r_start, r_end + 1):
            if kinds[i][j] != BLANK:
                last = df.iat[i, j]
            filled[(i, j)] = last
    return filled


def extract_tidy(df: pd.DataFrame, result: dict, max_rows: int = 8) -> Optional[dict]:
    """Build a normalized table for a tidy or matrix sheet.

    Returns ``{columns, records, n_records, n_columns}`` or ``None`` when the
    orientation is not extractable.
    """
    orient = result["orientation"]
    meta = result["meta"]
    kinds = _kinds(df)

    if orient == "tidy":
        return _extract_tidy_table(df, kinds, meta, max_rows)
    if orient == "matrix":
        return _extract_matrix(df, kinds, meta, max_rows)
    return None


def _row_is_header_like(row, c0, c1, widest) -> bool:
    cnt = _counts(row, c0, c1)
    filled = cnt[NUMBER] + cnt[DATE] + cnt[TEXT]
    if filled == 0 or (widest and filled < 0.5 * widest):
        return False
    return cnt[TEXT] >= 0.5 * filled


def _fills_gaps(kinds, block_rows, cand, c0, c1) -> bool:
    """True when row ``cand`` is a *complementary* header layer: mostly text,
    and most of its filled cells sit under columns the block so far left blank.

    Real-world pattern: row A carries the period labels on the right, row B
    carries the field names on the left — together they form one header. Data
    rows never look like this: their cells sit *under* the filled header
    columns, so the gap fraction stays low and they are rejected.
    """
    cand_cols = [j for j in range(c0, c1 + 1) if kinds[cand][j] != BLANK]
    if len(cand_cols) < 2:
        return False
    text = sum(kinds[cand][j] == TEXT for j in cand_cols)
    if text < 0.5 * len(cand_cols):
        return False
    gaps = sum(all(kinds[r][j] == BLANK for r in block_rows) for j in cand_cols)
    return gaps >= 0.6 * len(cand_cols)


def _detect_header_block(kinds, start, r1, c0, c1, widest, max_span=3) -> List[int]:
    """Consecutive header-like rows starting at ``start`` (multi-row headers).

    Stops at the first non-header row so at least one data row remains; if the
    block would swallow everything, fall back to a single header row.

    A row only joins the block if the layer above it has blank cells: stacked
    headers exist because of horizontally-merged group labels, which always
    leave gaps. A fully-filled header row is complete on its own — without this
    guard, text-heavy *data* rows (name lists etc.) get eaten into the header.
    """
    rows = [start]
    i = start + 1
    while (i <= r1 and len(rows) < max_span
           and any(kinds[rows[-1]][j] == BLANK for j in range(c0, c1 + 1))
           and ((_row_is_header_like(kinds[i], c0, c1, widest)
                 # extension layers must be pure text: a numeric cell means it
                 # is a data row, not a sub-header (years live in layer one)
                 and not any(kinds[i][j] == NUMBER for j in range(c0, c1 + 1)))
                or _fills_gaps(kinds, rows, i, c0, c1))):
        rows.append(i)
        i += 1
    if rows[-1] >= r1:
        return [start]
    return rows


def _hffill(vals, kinds_row, cols) -> List[object]:
    """Forward-fill a header layer across columns (spreads merged group labels)."""
    out, last = [], None
    for k, j in enumerate(cols):
        if kinds_row[j] != BLANK:
            last = vals[k]
        out.append(last)
    return out


def _merge_header_names(df, kinds, header_rows, cols) -> List[str]:
    if len(header_rows) == 1:
        return _unique_names([df.iat[header_rows[0], j] for j in cols])
    layers = []
    for ridx, hr in enumerate(header_rows):
        vals = [df.iat[hr, j] for j in cols]
        if ridx < len(header_rows) - 1:      # group rows get horizontal ffill
            vals = _hffill(vals, kinds[hr], cols)
            # a value spanning >80% of the columns after ffill is a title
            # banner, not a group label — it adds no column information
            counts: Dict[str, int] = {}
            for v in vals:
                if not is_blank(v):
                    s = str(v).strip()
                    counts[s] = counts.get(s, 0) + 1
            banners = {s for s, n in counts.items() if n > 0.8 * len(cols)}
            if banners:
                vals = [None if (not is_blank(v) and str(v).strip() in banners)
                        else v for v in vals]
        layers.append(vals)
    merged = []
    for k in range(len(cols)):
        parts = [str(layer[k]).strip() for layer in layers if not is_blank(layer[k])]
        merged.append(" / ".join(dict.fromkeys(parts)) if parts else "")
    return _unique_names(merged)


def _extract_tidy_table(df, kinds, meta, max_rows) -> dict:
    r0, r1, c0, c1 = meta["box"]
    cols = list(range(c0, c1 + 1))
    widest = max((sum(k != BLANK for k in kinds[i][c0:c1 + 1])
                  for i in range(r0, r1 + 1)), default=0)

    if "header_rows" in meta:            # composite header already resolved
        header_rows = meta["header_rows"]
    else:
        header_rows = _detect_header_block(kinds, meta["header_row"], r1, c0, c1, widest)
    names = _merge_header_names(df, kinds, header_rows, cols)
    data_start = header_rows[-1] + 1

    # Label columns (mostly text below the header) get forward-filled so merged
    # cells like a block label spanning several rows are carried down.
    label_cols = _label_cols(kinds, data_start, r1, c0, c1 + 1)
    ff = _ffill_label_cols(df, kinds, label_cols, data_start, r1)

    accs = [ColumnAcc(name) for name in names]
    records = []
    col_values = [[] for _ in cols]          # full coerced columns for Step 4
    row_numbers = []                          # 1-based sheet rows, as Excel shows
    total = 0
    for i in range(data_start, r1 + 1):
        if not any(kinds[i][j] != BLANK for j in cols):
            continue
        total += 1
        row_numbers.append(i + 1)
        rec = {}
        for idx, (acc, name, j) in enumerate(zip(accs, names, cols)):
            v = ff[(i, j)] if (i, j) in ff else df.iat[i, j]
            k = cell_kind(v)                 # profile the effective (ffilled) value
            cv = coerce_value(v)
            acc.add(k, cv)                   # Step 3: streaming column stats
            col_values[idx].append(cv)
            if len(records) < max_rows:
                rec[name] = cv
        if len(records) < max_rows:
            records.append(rec)

    # Step 4: reconciliation checks over the numeric columns. Rows are labelled
    # by the first text column so findings point at "Semences, row 10", not
    # just an index.
    numeric_cols = {
        name: [v if isinstance(v, (int, float)) and not isinstance(v, bool)
               else None for v in vals]
        for acc, name, vals in zip(accs, names, col_values)
        if acc.dtype() == "number"
    }
    label_idx = next((k for k, a in enumerate(accs) if a.dtype() == "text"), None)
    row_labels = ([str(v) if v is not None else None
                   for v in col_values[label_idx]]
                  if label_idx is not None else [None] * total)
    checks = run_checks(names, numeric_cols, row_numbers, row_labels) \
        if numeric_cols else None

    return {"columns": names, "records": records, "n_records": total,
            "n_columns": len(names),
            "column_types": [a.profile() for a in accs],
            "header_rows": header_rows,
            "summary": table_summary(accs, total),
            "checks": checks}


def _extract_matrix(df, kinds, meta, max_rows) -> dict:
    r0, r1, c0, c1 = meta["box"]
    dr = meta["date_row"]
    period_cols = meta["period_cols"]
    label_cols = meta["label_cols"]

    label_names = _unique_names([df.iat[dr, j] for j in label_cols]) \
        if label_cols else []
    # Fall back to generic label names if the date-row cells over the label
    # columns were blank.
    label_names = [n if n else f"label_{k}" for k, n in enumerate(label_names)]

    ff = _ffill_label_cols(df, kinds, label_cols, dr + 1, r1)

    columns = label_names + ["period", "value"]
    accs = [ColumnAcc(n) for n in columns]
    label_accs, period_acc, value_acc = accs[:-2], accs[-2], accs[-1]

    # Coerce each period header once; every long row under it reuses this.
    periods = {p: coerce_value(df.iat[dr, p]) for p in period_cols}

    records = []
    total = n_series = 0
    for i in range(dr + 1, r1 + 1):
        # skip fully blank interior rows
        if not any(kinds[i][p] != BLANK for p in period_cols):
            continue
        n_series += 1
        # effective (merged-cell ffilled) label values for this series row
        label_vals = []
        for j in label_cols:
            v = ff.get((i, j), df.iat[i, j])
            label_vals.append((cell_kind(v), coerce_value(v)))
        labels = {name: cv for name, (_, cv) in zip(label_names, label_vals)}
        for p in period_cols:
            if kinds[i][p] == BLANK:
                continue
            total += 1
            for acc, (k, cv) in zip(label_accs, label_vals):
                acc.add(k, cv)
            period_acc.add(kinds[dr][p], periods[p])
            value_acc.add(kinds[i][p], coerce_value(df.iat[i, p]))
            if len(records) < max_rows:
                rec = dict(labels)
                rec["period"] = periods[p]
                rec["value"] = coerce_value(df.iat[i, p])
                records.append(rec)

    if meta.get("axis") == "year":
        span = sorted(y for p in period_cols
                      if (y := _year_value(df.iat[dr, p])) is not None)
    else:
        span = sorted(str(v) for p, v in periods.items()
                      if kinds[dr][p] == DATE and v is not None)
    extra = {
        "n_series": n_series,
        "n_periods": len(period_cols),
        "period_min": span[0] if span else None,
        "period_max": span[-1] if span else None,
    }
    return {"columns": columns, "records": records, "n_records": total,
            "n_columns": len(columns),
            "column_types": [a.profile() for a in accs],
            "header_rows": [dr],
            "summary": table_summary(accs, total, extra)}


# --------------------------------------------------------------------------- #
# Side-by-side tables (panels)
# --------------------------------------------------------------------------- #
def _split_panels(kinds, box) -> List[Tuple[int, int]]:
    """Column ranges of side-by-side tables, split on fully-blank columns.

    Only ranges at least 2 columns wide count as panels; single stray columns
    are ignored rather than reported as tables.
    """
    r0, r1, c0, c1 = box
    panels, start = [], None
    for j in range(c0, c1 + 2):
        blank = j > c1 or all(kinds[i][j] == BLANK for i in range(r0, r1 + 1))
        if blank:
            if start is not None and j - start >= 2:
                panels.append((start, j - 1))
            start = None
        elif start is None:
            start = j
    return panels


def _orient_single(df: pd.DataFrame, max_rows: int) -> dict:
    result = classify(df)
    return {
        "orientation": result["orientation"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "tidy": extract_tidy(df, result, max_rows),
    }


def orient_sheet(df: pd.DataFrame, max_rows: int = 8) -> dict:
    """Classify + extract. Sheets holding several side-by-side tables
    (separated by blank columns) are split and each panel handled on its own;
    the sheet then reports orientation ``multi`` with per-panel results."""
    kinds = _kinds(df)
    box = bounding_box(kinds)
    if box is not None:
        panels = _split_panels(kinds, box)
        if len(panels) >= 2:
            sub = []
            for lo, hi in panels:
                res = _orient_single(df.iloc[:, lo:hi + 1], max_rows)
                res["col_start"], res["col_end"] = lo, hi
                sub.append(res)
            recognized = sum(s["orientation"] != "unknown" for s in sub)
            # Only worth splitting when >= 2 panels are real tables; otherwise
            # the blank columns were just layout spacing.
            if recognized >= 2:
                return {
                    "orientation": "multi",
                    "confidence": round(min(s["confidence"] for s in sub
                                            if s["orientation"] != "unknown"), 3),
                    "reason": (f"{len(panels)} side-by-side tables separated "
                               f"by blank columns"),
                    "tidy": None,
                    "panels": sub,
                }
    out = _orient_single(df, max_rows)
    out["panels"] = None
    return out
