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

from .celltypes import cell_kind, grid_kinds, is_blank, BLANK, DATE, NUMBER, TEXT
from .charts import profile_chart, timeseries_chart, year_series_chart
from .checks import detect_total_rows, run_checks
from .eda import run_eda_df
from .semantics import build_matrix_semantics, build_semantics
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
    return grid_kinds(df)


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


# bare month names, full form only — the strict list the banner-year rule
# accepts; abbreviations stay refused (a fabricated period is worse than none)
_BARE_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "decembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
_BANNER_YEAR_RE = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")


def _bare_month(v) -> Optional[int]:
    if not isinstance(v, str):
        return None
    return _BARE_MONTHS.get(v.strip().lower())


def _month_header_rows(df, kinds, r0, r1, c0, c1) -> List[int]:
    """Rows whose period columns are bare month names (≥ MIN_DATE_RUN)."""
    out = []
    for i in range(r0, r1 + 1):
        n = sum(1 for j in range(c0, c1 + 1)
                if _bare_month(df.iat[i, j]) is not None)
        if n >= MIN_DATE_RUN:
            out.append(i)
    return out


def _find_month_row(df, kinds, r0, r1, c0, c1):
    """The banner-year rule: real plan sheets head their columns with bare
    'Janvier'…'Décembre' and put the year in the block banner ('ESTIMATION
    PRODUCTION 2026'). A bare month is never a date by itself, so the row
    qualifies ONLY when exactly ONE distinct 4-digit year appears among the
    text cells of the whole candidate region (month cells excluded). Zero
    years, or two ('PRODUCTION 2025&2026'), refuse — which column belongs
    to which year is unknowable. Returns (row, period_cols, strength,
    {col: 'YYYY-MM-01'}) or None."""
    headers = _month_header_rows(df, kinds, r0, min(r1, r0 + SCAN_ROWS - 1),
                                 c0, c1)
    if not headers:
        return None
    i = headers[0]
    month_cols = [j for j in range(c0, c1 + 1)
                  if _bare_month(df.iat[i, j]) is not None]
    cnt = _counts(kinds[i], c0, c1)
    filled = cnt[NUMBER] + cnt[DATE] + cnt[TEXT]
    if not filled or len(month_cols) / filled < DATE_AXIS_FRAC:
        return None
    years: set = set()
    month_cells = {(r, j) for r in _month_header_rows(df, kinds, r0, r1, c0, c1)
                   for j in range(c0, c1 + 1)
                   if _bare_month(df.iat[r, j]) is not None}
    for r in range(r0, r1 + 1):
        for j in range(c0, c1 + 1):
            v = df.iat[r, j]
            if not isinstance(v, str) or (r, j) in month_cells:
                continue
            years.update(int(y) for y in _BANNER_YEAR_RE.findall(v))
    if len(years) != 1:
        return None
    year = years.pop()
    period_map = {j: f"{year:04d}-{_bare_month(df.iat[i, j]):02d}-01"
                  for j in month_cols}
    strength = (len(month_cols) / filled) * min(1.0, len(month_cols) / 5.0)
    return i, month_cols, strength, period_map


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


def classify(df: pd.DataFrame, kinds: Optional[List[List[str]]] = None) -> dict:
    """Return orientation + confidence + extraction metadata for one sheet."""
    if kinds is None:
        kinds = _kinds(df)
    box = bounding_box(kinds)
    if box is None:
        return {"orientation": "unknown", "confidence": 0.0, "meta": {},
                "reason": "sheet is empty"}
    r0, r1, c0, c1 = box

    # 1) Matrix: a strong horizontal period axis — real dates, strictly
    #    increasing year numbers, or bare month names under one banner year
    #    — with a numeric interior and at least one text label column left.
    month_found = _find_month_row(df, kinds, r0, r1, c0, c1)
    for axis, found, period_map in (
            ("date", _find_date_row(kinds, r0, r1, c0, c1), None),
            ("year", _find_year_row(df, kinds, r0, r1, c0, c1), None),
            ("date", month_found[:3] if month_found else None,
             month_found[3] if month_found else None)):
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
            meta = {"date_row": date_row, "period_cols": period_cols,
                    "label_cols": labels, "box": box, "axis": axis}
            if period_map:
                meta["period_map"] = period_map
            return {
                "orientation": "matrix",
                "confidence": conf,
                "meta": meta,
                "reason": (f"{axis} axis across columns at row {date_row} "
                           f"({len(period_cols)} periods), interior "
                           f"{interior:.0%} numeric"
                           + (" — bare months under one banner year"
                              if period_map else "")),
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
    header = guess_header_row(df, kinds=kinds)
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


def extract_tidy(df: pd.DataFrame, result: dict, max_rows: int = 8,
                 kinds: Optional[List[List[str]]] = None,
                 name: str = "") -> Optional[dict]:
    """Build a normalized table for a tidy or matrix sheet.

    Returns ``{columns, records, n_records, n_columns}`` or ``None`` when the
    orientation is not extractable. ``name`` (the sheet name) labels the
    measure of a matrix's long form — it is what the numbers are.
    """
    orient = result["orientation"]
    meta = result["meta"]
    if kinds is None:
        kinds = _kinds(df)

    if orient == "tidy":
        return _extract_tidy_table(df, kinds, meta, max_rows)
    if orient == "matrix":
        return _extract_matrix(df, kinds, meta, max_rows, name)
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
    # column name -> row ordinals whose effective value was forward-filled
    # from a merged cell above: the value is real, the CELL is blank — such
    # positions must never be cited as a figure's source
    ghost_rows: Dict[str, set] = {}
    total = 0
    for i in range(data_start, r1 + 1):
        if not any(kinds[i][j] != BLANK for j in cols):
            continue
        total += 1
        row_numbers.append(i + 1)
        rec = {}
        for idx, (acc, name, j) in enumerate(zip(accs, names, cols)):
            if (i, j) in ff:
                v = ff[(i, j)]
                ghost_rows.setdefault(name, set()).add(total - 1)
            else:
                v = df.iat[i, j]
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
    # just an index. LABEL columns never qualify: they were chosen for
    # forward-fill because their raw cells are majority text, so a numeric
    # profile there is an artifact of the ffill — summing it would count
    # merged-cell ghosts (one real 500 rendering as 1500).
    label_set = set(label_cols)
    numeric_cols = {
        name: [v if isinstance(v, (int, float)) and not isinstance(v, bool)
               else None for v in vals]
        for acc, name, vals, j in zip(accs, names, col_values, cols)
        if acc.dtype() == "number" and j not in label_set
    }
    label_idx = next((k for k, a in enumerate(accs) if a.dtype() == "text"), None)
    row_labels = ([str(v) if v is not None else None
                   for v in col_values[label_idx]]
                  if label_idx is not None else [None] * total)
    totals = detect_total_rows(names, numeric_cols, row_labels) \
        if numeric_cols else []
    col_map = {name: j for name, j in zip(names, cols)}
    checks = run_checks(names, numeric_cols, row_numbers, row_labels,
                        totals=totals, col_map=col_map) if numeric_cols else None

    summary = table_summary(accs, total)
    totals_idx = {t["i"] for t in totals}
    # a year column in rows makes a real time series; else the column profile
    chart = (year_series_chart(numeric_cols, totals_idx,
                               row_numbers=row_numbers, col_map=col_map,
                               ghost_rows=ghost_rows)
             or profile_chart(names, numeric_cols, totals_idx)) \
        if numeric_cols else None
    if chart:
        summary["chart"] = chart

    # Step 6: EDA over the FULL columns (col_values), never the record preview.
    df_full = pd.DataFrame({name: vals for name, vals in zip(names, col_values)}) \
        if total else None
    eda = run_eda_df(df_full) if total else None

    # Step 8: semantic schema + metric engine for the storytelling layer.
    # Totals rows are derived, not data — summing them would double-count.
    semantics = None
    if df_full is not None:
        data_rows = df_full.drop(index=[t["i"] for t in totals], errors="ignore")
        semantics = build_semantics(data_rows.reset_index(drop=True))

    # Step 7 raw material: top line items by the detected target column
    # (label x value), data rows only — feeds the business-model breakdowns.
    # EDA's numeric test (>=5% parseable) is looser than the streaming dtype;
    # its target can be a mostly-text column we did not keep — guard for it.
    if eda and eda.get("target_column") in numeric_cols and label_idx is not None:
        tcol = names.index(eda["target_column"])
        skip = {t["i"] for t in totals}
        items = [
            {"label": str(col_values[label_idx][i]),
             "value": round(float(v), 4)}
            for i, v in enumerate(numeric_cols[eda["target_column"]])
            if v is not None and i not in skip
            and col_values[label_idx][i] is not None
        ]
        items.sort(key=lambda x: -abs(x["value"]))
        if len(items) >= 3:
            summary["breakdown"] = {
                "label_col": names[label_idx],
                "value_col": eda["target_column"],
                "items": items[:10],
                "n_items": len(items),
            }

    return {"columns": names, "records": records, "n_records": total,
            "n_columns": len(names),
            "column_types": [a.profile() for a in accs],
            "header_rows": header_rows,
            "summary": summary,
            "checks": checks,
            "eda": eda,
            "semantics": semantics,
            # 1-based sheet rows detected as summary/totals rows
            "total_rows": [row_numbers[t["i"]] for t in totals] or None}


def _extract_matrix(df, kinds, meta, max_rows, name: str = "") -> dict:
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
    # A banner-year matrix carries its synthesized periods in period_map
    # ("Janvier" + "… 2026" -> "2026-01-01"); the cells stay bare months.
    pm = meta.get("period_map")
    periods = ({p: pm[p] for p in period_cols} if pm
               else {p: coerce_value(df.iat[dr, p]) for p in period_cols})

    p_index = {p: k for k, p in enumerate(period_cols)}
    series_values: Dict[str, Dict[int, float]] = {}   # for the dashboard chart
    # label -> {period_index: [[1-based row, 0-based col], ...]} — the source
    # cells behind each summed value; _finalize_cells turns them into A1 refs
    series_cells: Dict[str, Dict[int, list]] = {}

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
        series_key = " / ".join(str(cv) for _, cv in label_vals
                                if cv is not None) or f"series {n_series}"
        bucket = series_values.setdefault(series_key, {})
        cbucket = series_cells.setdefault(series_key, {})
        for p in period_cols:
            if kinds[i][p] == BLANK:
                continue
            total += 1
            cv = coerce_value(df.iat[i, p])
            for acc, (k, lv) in zip(label_accs, label_vals):
                acc.add(k, lv)
            period_acc.add(DATE if pm else kinds[dr][p], periods[p])
            value_acc.add(kinds[i][p], cv)
            if isinstance(cv, (int, float)) and not isinstance(cv, bool):
                k = p_index[p]
                bucket[k] = bucket.get(k, 0.0) + cv
                cbucket.setdefault(k, []).append([i + 1, p])
            if len(records) < max_rows:
                rec = dict(labels)
                rec["period"] = periods[p]
                rec["value"] = cv
                records.append(rec)

    if meta.get("axis") == "year":
        span = sorted(y for p in period_cols
                      if (y := _year_value(df.iat[dr, p])) is not None)
    elif pm:
        span = sorted(pm.values())
    else:
        span = sorted(str(v) for p, v in periods.items()
                      if kinds[dr][p] == DATE and v is not None)
    extra = {
        "n_series": n_series,
        "n_periods": len(period_cols),
        "period_min": span[0] if span else None,
        "period_max": span[-1] if span else None,
    }
    summary = table_summary(accs, total, extra)
    chart = timeseries_chart([periods[p] for p in period_cols], series_values,
                             axis=meta.get("axis", "date"),
                             series_cells=series_cells)
    if chart:
        summary["chart"] = chart

    # Step 8: date-axis matrices drive the storytelling engine through their
    # long form. (Year-axis cross-tabs feed the business model instead.)
    semantics = None
    if meta.get("axis", "date") == "date":
        semantics = build_matrix_semantics(
            name, [periods[p] for p in period_cols], series_values)

    return {"columns": columns, "records": records, "n_records": total,
            "n_columns": len(columns),
            "column_types": [a.profile() for a in accs],
            "header_rows": [dr],
            "summary": summary,
            "semantics": semantics}


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


def _split_bands(kinds, box) -> List[Tuple[int, int]]:
    """Row ranges of vertically stacked tables, split on fully-blank rows.
    Only bands at least 2 rows tall count."""
    r0, r1, c0, c1 = box
    bands, start = [], None
    for i in range(r0, r1 + 2):
        blank = i > r1 or all(kinds[i][j] == BLANK for j in range(c0, c1 + 1))
        if blank:
            if start is not None and i - start >= 2:
                bands.append((start, i - 1))
            start = None
        elif start is None:
            start = i
    return bands


def _xl_col(j: int) -> str:
    """0-based column index -> Excel letters (0 -> A, 26 -> AA)."""
    out = ""
    j += 1
    while j > 0:
        j, r = divmod(j - 1, 26)
        out = chr(65 + r) + out
    return out


def _finalize_cells(res: dict, row_off: int = 0, col_off: int = 0) -> None:
    """Turn each mismatch's (row, col) into a real Excel cell address, shifting
    by the panel/band offsets when the table was classified on a slice.
    Chart series source cells get the same treatment: the [row, col] pairs
    recorded at extraction become absolute A1 refs."""
    t = res.get("tidy")
    if not t:
        return
    if row_off:
        if t.get("header_rows"):
            t["header_rows"] = [h + row_off for h in t["header_rows"]]
        if t.get("total_rows"):
            t["total_rows"] = [r + row_off for r in t["total_rows"]]
    for f in t.get("checks") or []:
        for m in f.get("mismatches", []):
            m["row"] += row_off
            col = m.pop("col", None)
            m["cell"] = f"{_xl_col(col + col_off)}{m['row']}" if col is not None else None
    chart = (t.get("summary") or {}).get("chart") or {}
    for s in chart.get("series_all") or []:
        if s.get("cells"):
            s["cells"] = [
                [f"{_xl_col(c + col_off)}{r + row_off}" for r, c in refs]
                if refs else None
                for refs in s["cells"]]


def _orient_single(df: pd.DataFrame, max_rows: int,
                   kinds: Optional[List[List[str]]] = None,
                   name: str = "") -> dict:
    if kinds is None:
        kinds = _kinds(df)
    result = classify(df, kinds)
    return {
        "orientation": result["orientation"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "tidy": extract_tidy(df, result, max_rows, kinds, name),
    }


def orient_sheet(df: pd.DataFrame, max_rows: int = 8,
                 kinds: Optional[List[List[str]]] = None,
                 name: str = "") -> dict:
    """Classify + extract. Sheets holding several side-by-side tables
    (separated by blank columns) are split and each panel handled on its own;
    the sheet then reports orientation ``multi`` with per-panel results.
    Vertically stacked tables (separated by blank rows) are tried as a
    fallback when the sheet as a whole cannot be classified."""
    if kinds is None:
        kinds = _kinds(df)
    box = bounding_box(kinds)
    if box is not None:
        panels = _split_panels(kinds, box)
        if len(panels) >= 2:
            sub = []
            for lo, hi in panels:
                pk = [row[lo:hi + 1] for row in kinds]
                res = _orient_single(df.iloc[:, lo:hi + 1], max_rows, pk, name)
                _finalize_cells(res, 0, lo)
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

    out = _orient_single(df, max_rows, kinds, name)
    _finalize_cells(out)
    out["panels"] = None

    # Fallback for vertically stacked sheets (title / table / table / notes):
    # only when the sheet as a whole declined — sheets that already classify
    # (e.g. with internal spacer rows) are never shredded into bands.
    # EXCEPTION: several bare-month header rows in one sheet mean stacked
    # period blocks (often different banner years — 'PRODUCTION 2025' over
    # one, 'ESTIMATION 2026' over the next); a single-table read of that is
    # wrong however confident it looks, so the bands get a try first.
    force_bands = (out["orientation"] != "matrix" and box is not None
                   and len(_month_header_rows(df, kinds, box[0], box[1],
                                              box[2], box[3])) >= 2)
    if (out["orientation"] == "unknown" or force_bands) and box is not None:
        bands = _split_bands(kinds, box)
        if len(bands) >= 2:
            sub = []
            for lo, hi in bands:
                res = _orient_single(df.iloc[lo:hi + 1], max_rows,
                                     kinds[lo:hi + 1], name)
                _finalize_cells(res, lo, 0)
                res["row_start"], res["row_end"] = lo, hi
                sub.append(res)
            recognized = sum(s["orientation"] != "unknown" for s in sub)
            if recognized >= 2:
                return {
                    "orientation": "multi",
                    "confidence": round(min(s["confidence"] for s in sub
                                            if s["orientation"] != "unknown"), 3),
                    "reason": (f"{len(bands)} stacked tables separated "
                               f"by blank rows"),
                    "tidy": None,
                    "panels": sub,
                }
    return out
