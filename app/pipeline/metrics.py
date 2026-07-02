"""Step 3: per-column statistics + table-level summary over extracted tables.

The accumulators are fed *during* extraction (one streaming pass, no second
read of the sheet) with the effective cell kind and the coerced value. They
stay honest in two ways:

* a ``mixed`` column reports sub-stats **per kind** — numeric stats explicitly
  cover only the numeric cells, never a silently-dropped remainder;
* unbounded tracking is capped (``DISTINCT_CAP``): when a column has more
  distinct values than we track, ``distinct`` is reported as ``None`` with
  ``distinct_capped: true`` instead of a wrong count.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from .celltypes import BLANK, DATE, NUMBER, TEXT

# A detected column dtype needs this share of one kind, else it is "mixed".
DOMINANT_FRAC = 0.6

# Stop tracking distinct values past this many; report honestly that we did.
DISTINCT_CAP = 1000

TOP_VALUES = 5


def _round(x: float) -> float:
    return round(float(x), 4)


class ColumnAcc:
    """Streaming accumulator for one output column."""

    def __init__(self, name: str):
        self.name = name
        self.rows = 0                      # rows seen (incl. blanks)
        self.counts = {NUMBER: 0, DATE: 0, TEXT: 0}
        # numeric
        self._sum = 0.0
        self._min: Optional[float] = None
        self._max: Optional[float] = None
        self._zeros = 0
        self._negatives = 0
        # dates (coerced to ISO strings -> lexicographic order is chronological)
        self._dmin: Optional[str] = None
        self._dmax: Optional[str] = None
        self._ddistinct: set = set()
        self._dcapped = False
        # text
        self._top: Counter = Counter()
        self._tcapped = False

    def add(self, kind: str, value: Any) -> None:
        """``kind`` is the effective cell kind; ``value`` the coerced cell."""
        self.rows += 1
        if kind == BLANK or value is None:
            return
        self.counts[kind] += 1
        if kind == NUMBER and isinstance(value, (int, float)):
            f = float(value)
            self._sum += f
            self._min = f if self._min is None else min(self._min, f)
            self._max = f if self._max is None else max(self._max, f)
            if f == 0:
                self._zeros += 1
            elif f < 0:
                self._negatives += 1
        elif kind == DATE:
            s = str(value)
            self._dmin = s if self._dmin is None else min(self._dmin, s)
            self._dmax = s if self._dmax is None else max(self._dmax, s)
            if len(self._ddistinct) < DISTINCT_CAP:
                self._ddistinct.add(s)
            elif s not in self._ddistinct:
                self._dcapped = True
        elif kind == TEXT:
            s = str(value)
            if s in self._top or len(self._top) < DISTINCT_CAP:
                self._top[s] += 1
            else:
                self._tcapped = True

    # ------------------------------------------------------------------ #
    @property
    def filled(self) -> int:
        return sum(self.counts.values())

    def dtype(self) -> str:
        if not self.filled:
            return "empty"
        dom = max(self.counts, key=self.counts.get)
        return dom if self.counts[dom] / self.filled >= DOMINANT_FRAC else "mixed"

    def _number_stats(self) -> Dict[str, Any]:
        n = self.counts[NUMBER]
        return {
            "n": n,
            "min": _round(self._min),
            "max": _round(self._max),
            "sum": _round(self._sum),
            "mean": _round(self._sum / n),
            "zeros": self._zeros,
            "negatives": self._negatives,
        }

    def _date_stats(self) -> Dict[str, Any]:
        return {
            "n": self.counts[DATE],
            "min": self._dmin,
            "max": self._dmax,
            "distinct": None if self._dcapped else len(self._ddistinct),
            "distinct_capped": self._dcapped,
        }

    def _text_stats(self) -> Dict[str, Any]:
        return {
            "n": self.counts[TEXT],
            "distinct": None if self._tcapped else len(self._top),
            "distinct_capped": self._tcapped,
            "top": [{"value": v, "count": c}
                    for v, c in self._top.most_common(TOP_VALUES)],
        }

    def profile(self) -> Dict[str, Any]:
        """Column profile: the Step-2 keys plus a ``stats`` block per kind
        actually present (so 'mixed' columns are broken down, not averaged)."""
        stats: Dict[str, Any] = {}
        if self.counts[NUMBER]:
            stats["number"] = self._number_stats()
        if self.counts[DATE]:
            stats["date"] = self._date_stats()
        if self.counts[TEXT]:
            stats["text"] = self._text_stats()
        return {
            "name": self.name,
            "dtype": self.dtype(),
            "fill": _round(self.filled / self.rows) if self.rows else 0.0,
            "number": self.counts[NUMBER],
            "date": self.counts[DATE],
            "text": self.counts[TEXT],
            "stats": stats or None,
        }


def table_summary(accs: List[ColumnAcc], n_records: int,
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Whole-table roll-up: size, completeness, and orientation extras."""
    total_cells = n_records * len(accs)
    filled = sum(a.filled for a in accs)
    out: Dict[str, Any] = {
        "n_records": n_records,
        "n_columns": len(accs),
        "cell_fill": _round(filled / total_cells) if total_cells else 0.0,
        "numeric_columns": sum(a.dtype() == "number" for a in accs),
        "date_columns": sum(a.dtype() == "date" for a in accs),
        "text_columns": sum(a.dtype() == "text" for a in accs),
        "mixed_columns": sum(a.dtype() == "mixed" for a in accs),
        "empty_columns": sum(a.dtype() == "empty" for a in accs),
    }
    if extra:
        out.update(extra)
    return out
