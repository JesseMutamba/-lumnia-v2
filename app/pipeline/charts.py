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


# Keep EVERY series so the business-model layer can role-tag them — the
# top-5 fold is only for plotting sanity. Real operational matrices carry
# 40-70 label rows (the anchor extraction sheet: 65), so the ceiling sits
# well above that; a pathological thousands-of-rows matrix still gets no
# series_all rather than a megabyte payload.
MAX_FULL_SERIES = 128


def timeseries_chart(periods: List[object],
                     series_values: Dict[str, Dict[int, float]],
                     axis: str = "date",
                     series_cells: Optional[Dict[str, Dict[int, list]]] = None
                     ) -> Optional[dict]:
    """Build the timeseries payload.

    ``periods``: coerced period labels in axis order.
    ``series_values``: label -> {period_index: summed numeric value}.
    ``series_cells``: label -> {period_index: [[row, col], ...]} — the source
    cells each summed value came from, same keys as ``series_values``. They
    ride on ``series_all`` only (the model layer's series), never the folded
    plotting series; ``_finalize_cells`` later turns the pairs into absolute
    A1 refs.
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

    out = {
        "kind": "timeseries",
        "axis": axis,
        "periods": [str(p) if not isinstance(p, (int, float)) else p
                    for p in periods[:n]],
        "series": series,
        "other": other,
        "truncated": truncated,
    }
    # small charts keep EVERY series so the business-model layer can
    # role-tag them — year axes for projections, date axes for actuals
    if len(ranked) <= MAX_FULL_SERIES:
        out["series_all"] = []
        for label, vals, _ in ranked:
            entry = {"label": str(label), "values": row(vals)}
            cmap = (series_cells or {}).get(label)
            if cmap:
                entry["cells"] = [cmap.get(i) for i in range(n)]
            out["series_all"].append(entry)
    return out


def _year_col(numeric_cols: Dict[str, List[Optional[float]]],
              totals_idx: set) -> Optional[str]:
    """First column whose data rows are ≥90% whole calendar years (≥3 of
    them) — a period label living in rows instead of across the header."""
    for name, vals in numeric_cols.items():
        data = [v for i, v in enumerate(vals)
                if v is not None and i not in totals_idx]
        if len(data) < 3:
            continue
        years = [v for v in data
                 if float(v).is_integer() and 1900 <= v <= 2100]
        if len(years) / len(data) >= 0.9 \
                and len({int(v) for v in years}) >= 3:
            return name
    return None


def year_series_chart(numeric_cols: Dict[str, List[Optional[float]]],
                      totals_idx: set,
                      row_numbers: Optional[List[int]] = None,
                      col_map: Optional[Dict[str, int]] = None,
                      ghost_rows: Optional[Dict[str, set]] = None
                      ) -> Optional[dict]:
    """A tidy table with a bare year column (2025, 2026…) is a time series
    stored in rows: sum every other numeric column by year and emit the same
    ``axis="year"`` payload matrix sheets produce — so the business-model
    layer can role-tag the series. Data rows only; totals rows would
    double-count. Nothing year-like -> ``None`` (profile chart fallback).

    ``row_numbers`` (row ordinal -> 1-based sheet row) and ``col_map``
    (column name -> 0-based sheet column) let each summed value cite its
    source cells; without them the chart simply carries no ``cells``.
    ``ghost_rows`` (column name -> ordinals whose value was forward-filled
    from a merged cell) blocks the citation for any period such a row
    contributed to: the physical cell is blank, and citing a blank — or
    only the real subset — would be a false proof. No refs, no claim."""
    ycol = _year_col(numeric_cols, totals_idx)
    if ycol is None:
        return None

    rows_by_year: Dict[int, List[int]] = {}
    for i, y in enumerate(numeric_cols[ycol]):
        if i in totals_idx or y is None:
            continue
        if float(y).is_integer() and 1900 <= y <= 2100:
            rows_by_year.setdefault(int(y), []).append(i)
    if len(rows_by_year) < 3:
        return None

    periods = sorted(rows_by_year)
    pidx = {y: k for k, y in enumerate(periods)}
    series_values: Dict[str, Dict[int, float]] = {}
    series_cells: Dict[str, Dict[int, list]] = {}
    for name, vals in numeric_cols.items():
        # a second year-like column is another label, never a series
        if name == ycol or _year_col({name: vals}, totals_idx) == name:
            continue
        bucket: Dict[int, float] = {}
        cbucket: Dict[int, list] = {}
        ghosts = (ghost_rows or {}).get(name, set())
        for y, idxs in rows_by_year.items():
            keep = [i for i in idxs if vals[i] is not None]
            if keep:
                bucket[pidx[y]] = sum(vals[i] for i in keep)
                if row_numbers is not None and col_map and name in col_map \
                        and not any(i in ghosts for i in keep):
                    cbucket[pidx[y]] = [[row_numbers[i], col_map[name]]
                                        for i in keep]
        if bucket:
            series_values[name] = bucket
            if cbucket:
                series_cells[name] = cbucket
    if not series_values:
        return None
    return timeseries_chart(periods, series_values, axis="year",
                            series_cells=series_cells or None)


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
