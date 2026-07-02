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

from typing import Dict, List, Optional, Tuple

import pandas as pd

from .celltypes import cell_kind, is_blank, BLANK, DATE, NUMBER, TEXT
from .coerce import coerce_value
from .jsonsafe import jsonify
from .metrics import ColumnAcc, table_summary
from .profile import guess_header_row

# Tunables for the heuristics (documented at point of use).
MIN_DATE_RUN = 3          # a period axis needs at least this many date cells
DATE_AXIS_FRAC = 0.6      # ... and this fraction of the axis's filled cells
INTERIOR_NUMERIC = 0.5    # matrix interior must be at least this numeric
SCAN_ROWS = 15            # how deep to look for a period/header row


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

    # 1) Matrix: a strong horizontal date axis with a numeric interior and at
    #    least one text label column to the left.
    dr = _find_date_row(kinds, r0, r1, c0, c1)
    if dr is not None:
        date_row, period_cols, strength = dr
        interior = _interior_numeric_frac(kinds, date_row, period_cols, r1)
        labels = _label_cols(kinds, date_row, r1, c0, period_cols[0])
        if interior >= INTERIOR_NUMERIC and labels:
            conf = round(min(1.0, 0.5 * strength + 0.5 * interior), 3)
            return {
                "orientation": "matrix",
                "confidence": conf,
                "meta": {"date_row": date_row, "period_cols": period_cols,
                         "label_cols": labels, "box": box},
                "reason": (f"date axis across columns at row {date_row} "
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

    # 4) Decline honestly.
    return {"orientation": "unknown", "confidence": 0.0, "meta": {"box": box},
            "reason": "no orientation matched"}


# --------------------------------------------------------------------------- #
# Extraction -> normalized long/tidy table
# --------------------------------------------------------------------------- #
def _unique_names(raw: List[Optional[object]]) -> List[str]:
    names, seen = [], {}
    for j, v in enumerate(raw):
        base = str(v).strip() if v not in (None, "") else f"col_{j}"
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
           and _row_is_header_like(kinds[i], c0, c1, widest)):
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

    header_rows = _detect_header_block(kinds, meta["header_row"], r1, c0, c1, widest)
    names = _merge_header_names(df, kinds, header_rows, cols)
    data_start = header_rows[-1] + 1

    # Label columns (mostly text below the header) get forward-filled so merged
    # cells like a block label spanning several rows are carried down.
    label_cols = _label_cols(kinds, data_start, r1, c0, c1 + 1)
    ff = _ffill_label_cols(df, kinds, label_cols, data_start, r1)

    accs = [ColumnAcc(name) for name in names]
    records = []
    total = 0
    for i in range(data_start, r1 + 1):
        if not any(kinds[i][j] != BLANK for j in cols):
            continue
        total += 1
        rec = {}
        for acc, name, j in zip(accs, names, cols):
            v = ff[(i, j)] if (i, j) in ff else df.iat[i, j]
            k = cell_kind(v)                 # profile the effective (ffilled) value
            cv = coerce_value(v)
            acc.add(k, cv)                   # Step 3: streaming column stats
            if len(records) < max_rows:
                rec[name] = cv
        if len(records) < max_rows:
            records.append(rec)
    return {"columns": names, "records": records, "n_records": total,
            "n_columns": len(names),
            "column_types": [a.profile() for a in accs],
            "header_rows": header_rows,
            "summary": table_summary(accs, total)}


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

    period_dates = sorted(str(v) for p, v in periods.items()
                          if kinds[dr][p] == DATE and v is not None)
    extra = {
        "n_series": n_series,
        "n_periods": len(period_cols),
        "period_min": period_dates[0] if period_dates else None,
        "period_max": period_dates[-1] if period_dates else None,
    }
    return {"columns": columns, "records": records, "n_records": total,
            "n_columns": len(columns),
            "column_types": [a.profile() for a in accs],
            "header_rows": [dr],
            "summary": table_summary(accs, total, extra)}


def orient_sheet(df: pd.DataFrame, max_rows: int = 8) -> dict:
    """Convenience: classify + attach a tidy preview when extractable."""
    result = classify(df)
    tidy = extract_tidy(df, result, max_rows)
    return {
        "orientation": result["orientation"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "tidy": tidy,
    }
