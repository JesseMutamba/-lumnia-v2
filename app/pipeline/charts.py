"""Chart-ready series, computed at analysis time.

Stored reports keep only small record previews, so anything the dashboard
plots must be aggregated here, during extraction, and stored in the table
summary — bounded in size, never the raw records.

Two generic chart shapes:

* ``timeseries`` — from matrix sheets: value per period, one line per label,
  top ``MAX_SERIES`` by total magnitude, the rest folded into one "Other"
  series (never a hue per stray label).
* ``profile``    — from tidy sheets: per-column sums over the longest
  contiguous run of numeric columns (a Janvier..Décembre block), data rows
  only — detected totals rows are excluded, and a run-endpoint column that
  merely repeats the sum of the others (a Total column) is dropped.

Fail honest: nothing chartable -> ``None``, no chart key in the summary.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .checks import _numeric_runs, _close

MAX_SERIES = 5
MAX_PERIODS = 500


def _round(v: float) -> float:
    return round(float(v), 4)


def timeseries_chart(periods: List[object],
                     series_values: Dict[str, Dict[int, float]]) -> Optional[dict]:
    """Build the timeseries payload.

    ``periods``: coerced period labels in axis order.
    ``series_values``: label -> {period_index: summed numeric value}.
    """
    ranked = sorted(
        ((label, vals, sum(abs(v) for v in vals.values()))
         for label, vals in series_values.items() if vals),
        key=lambda t: -t[2],
    )
    if not ranked or all(t[2] == 0 for t in ranked):
        return None

    truncated = len(periods) > MAX_PERIODS
    n = min(len(periods), MAX_PERIODS)

    def row(vals: Dict[int, float]) -> List[Optional[float]]:
        return [_round(vals[i]) if i in vals else None for i in range(n)]

    top = ranked[:MAX_SERIES]
    rest = ranked[MAX_SERIES:]
    series = [{"label": str(label), "values": row(vals), "total": _round(tot)}
              for label, vals, tot in top]

    other = None
    if rest:
        merged: Dict[int, float] = {}
        for _, vals, _ in rest:
            for i, v in vals.items():
                merged[i] = merged.get(i, 0.0) + v
        other = {"label": f"Other ({len(rest)})", "values": row(merged),
                 "total": _round(sum(abs(v) for v in merged.values()))}

    return {
        "kind": "timeseries",
        "periods": [str(p) if not isinstance(p, (int, float)) else p
                    for p in periods[:n]],
        "series": series,
        "other": other,
        "truncated": truncated,
    }


def profile_chart(names: List[str],
                  numeric_cols: Dict[str, List[Optional[float]]],
                  totals_idx: set) -> Optional[dict]:
    """Per-column sums across the longest contiguous numeric-column run."""
    runs = _numeric_runs(names, set(numeric_cols))
    if not runs:
        return None
    run = max(runs, key=len)

    def colsum(name: str) -> float:
        return sum(v for i, v in enumerate(numeric_cols[name])
                   if v is not None and i not in totals_idx)

    sums = {name: colsum(name) for name in run}

    # A Total column sitting at either end of the run would dwarf the profile;
    # drop an endpoint that simply equals the sum of the other columns.
    for end in (run[0], run[-1]):
        others = [n for n in run if n != end]
        if len(others) >= 3 and _close(sum(sums[n] for n in others), sums[end]):
            run = others
    if len(run) < 3:
        return None

    y = [_round(sums[name]) for name in run]
    if all(v == 0 for v in y):
        return None
    return {"kind": "profile", "x": list(run), "y": y,
            "excluded_totals": len(totals_idx)}
