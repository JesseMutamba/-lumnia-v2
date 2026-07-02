"""General cell-type classification.

These helpers are the shared vocabulary for every heuristic in the pipeline.
They are deliberately *value-based* (never column-dtype based): real-world
spreadsheets read with ``header=None`` come back as ``object`` columns mixing
titles, dates, numbers and text in the same column, so we must judge each cell
on its own.
"""
from __future__ import annotations

import datetime as _dt
import math
import numbers
import re
from typing import Any

import numpy as np
import pandas as pd

# Cell kinds
BLANK = "blank"
NUMBER = "number"
DATE = "date"
TEXT = "text"

# A pragmatic date matcher for string cells: dd/mm/yyyy, yyyy-mm-dd, dd-mm-yy,
# dd.mm.yyyy, and month names are the common shapes in client files. We stay
# conservative: a string only counts as a date if it clearly looks like one.
_DATE_RE = re.compile(
    r"""^\s*(
        \d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}      # 31/12/2024, 2024-12-31, 31.12.24
        | \d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}
    )\s*$""",
    re.VERBOSE,
)

# Excel-style error literals that should be treated as blank/unknown, not text.
_ERROR_LITERALS = {
    "#div/0!", "#n/a", "#name?", "#null!", "#num!", "#ref!", "#value!", "#error!",
}


def is_blank(v: Any) -> bool:
    """True for None, NaN/NaT, empty/whitespace strings, and Excel error cells."""
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if v is pd.NaT:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return True
        if s.lower() in _ERROR_LITERALS:
            return True
    return False


def _looks_numeric(s: str) -> bool:
    s = s.strip().replace(" ", " ").replace(" ", "")
    if s == "":
        return False
    # Allow one thousands/decimal separator style; be permissive but not reckless.
    s = s.replace(",", ".") if s.count(",") == 1 and s.count(".") == 0 else s.replace(",", "")
    s = s.rstrip("%")
    try:
        float(s)
        return True
    except ValueError:
        return False


def cell_kind(v: Any) -> str:
    """Classify a single cell into BLANK / DATE / NUMBER / TEXT."""
    if is_blank(v):
        return BLANK
    # Real datetime objects (openpyxl/pandas hand these back for date cells).
    if isinstance(v, (_dt.datetime, _dt.date)):
        return DATE
    if isinstance(v, pd.Timestamp):
        return DATE
    # bool (incl. numpy bool_) is a flag, not a metric number.
    if isinstance(v, (bool, np.bool_)):
        return TEXT
    # numbers.Number catches python int/float AND numpy int64/float64 scalars.
    if isinstance(v, numbers.Number):
        return NUMBER
    if isinstance(v, str):
        s = v.strip()
        if _DATE_RE.match(s):
            return DATE
        if _looks_numeric(s):
            return NUMBER
        return TEXT
    # Anything exotic: call it text so it is at least visible downstream.
    return TEXT


def grid_kinds(df: pd.DataFrame) -> list:
    """Per-cell kinds for a whole sheet, as a list of row lists.

    Vectorises the blank check (``pd.isna`` over the whole array) so the
    per-cell classifier only runs on cells that actually hold something —
    on wide, sparse real-world sheets most cells are blank and this is the
    difference between milliseconds and tens of seconds.
    """
    vals = df.to_numpy(dtype=object)
    if vals.size == 0:
        return [[] for _ in range(vals.shape[0])]
    na = pd.isna(vals)
    return [
        [BLANK if na[i, j] else cell_kind(vals[i, j])
         for j in range(vals.shape[1])]
        for i in range(vals.shape[0])
    ]


def is_text(v: Any) -> bool:
    return cell_kind(v) == TEXT


def is_number(v: Any) -> bool:
    return cell_kind(v) == NUMBER


def is_date(v: Any) -> bool:
    return cell_kind(v) == DATE
