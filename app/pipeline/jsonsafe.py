"""Convert raw cell values into JSON-serialisable primitives.

Spreadsheet cells arrive as numpy scalars, pandas Timestamps, NaN/NaT, and
Excel error strings. FastAPI/JSON cannot encode several of these, so we
normalise: blanks and errors -> ``None``, dates -> ISO strings, numbers ->
plain ``int``/``float``, everything else -> ``str``.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Optional

import pandas as pd

from .celltypes import is_blank


def jsonify(v: Any) -> Optional[object]:
    if is_blank(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, bool):
        return v
    # numpy integer/floating expose .item(); fall back gracefully.
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except (ValueError, TypeError):
            return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(v, (int, str)):
        return v
    return str(v)
