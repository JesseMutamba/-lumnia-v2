"""Coerce raw cell values into canonical, computation-ready primitives.

``jsonify`` only makes a value JSON-safe; it leaves ``"1 234,5"`` as a string.
For downstream metrics we want the *meaning*: that cell is the number 1234.5.
This module does that normalisation, and — crucially — stays honest: if a value
does not cleanly parse as its detected kind, it is returned as text rather than
guessed into a wrong number.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Optional

import pandas as pd

from .celltypes import cell_kind, parse_month_year, BLANK, DATE, NUMBER, _DATE_RE

# ISO dates are year-first BY DEFINITION; feeding them through a dayfirst
# parse silently swaps day and month whenever the day is <= 12
# ("2025-01-05" -> May 1st). dayfirst is only for dd/mm/yyyy-style strings.
_ISO_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([T ].*)?$")


def coerce_number(v: Any) -> Optional[float]:
    """Parse a numeric cell (incl. French ``1 234,5`` and trailing ``%``).

    Returns ``int`` when integral, ``float`` otherwise, or ``None`` if it does
    not actually parse. Percentages keep their face value (``12%`` -> ``12``);
    we do not silently divide by 100.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if not isinstance(v, str):
        try:
            return float(v)  # numpy scalar, etc.
        except (TypeError, ValueError):
            return None
    s = v.strip().replace(" ", " ").replace(" ", "").rstrip("%")
    if s == "":
        return None
    # French decimal comma when there is no dot; otherwise commas are thousands.
    s = s.replace(",", ".") if (s.count(",") == 1 and s.count(".") == 0) \
        else s.replace(",", "")
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def coerce_date(v: Any) -> Optional[str]:
    """Parse a date cell to an ISO ``YYYY-MM-DD`` (or full ISO) string."""
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat() if v.normalize() == v else v.isoformat()
    if isinstance(v, _dt.datetime):
        return v.date().isoformat() if v.time() == _dt.time(0) else v.isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, str) and _DATE_RE.match(v.strip()):
        s = v.strip()
        ts = pd.to_datetime(s, dayfirst=not _ISO_RE.match(s), errors="coerce")
        if pd.notna(ts):
            return ts.date().isoformat() if ts.normalize() == ts else ts.isoformat()
    if isinstance(v, str):
        ym = parse_month_year(v.strip())   # "Jan 2026" / "févr. 26" -> month 1st
        if ym is not None:
            return _dt.date(ym[0], ym[1], 1).isoformat()
    return None


def coerce_value(v: Any) -> Optional[object]:
    """Canonicalise a cell by its detected kind: number -> num, date -> ISO,
    text -> str, blank/error -> ``None``."""
    kind = cell_kind(v)
    if kind == BLANK:
        return None
    if kind == NUMBER:
        n = coerce_number(v)
        return n if n is not None else (v if isinstance(v, str) else str(v))
    if kind == DATE:
        d = coerce_date(v)
        return d if d is not None else str(v)
    # text
    return v if isinstance(v, str) else str(v)
