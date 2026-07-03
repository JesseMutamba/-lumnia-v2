"""Step 7: the business model — semantic role-tagging over extracted series.

The dashboard's financial views (revenue trajectory, capex vs revenue, margin
curve, cost per tonne, scenario/simulation math) all need to know WHICH
extracted series is revenue, which is cost, which is volume. That mapping is
discovered here from series labels (French first, English second) over the
year-axis cross-tabs the pipeline already extracts — the same label-based,
fail-honest approach as target detection and totals rows. Nothing is ever
assumed: a role that isn't found simply isn't in the model, and the views
that need it don't render.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Role patterns, checked in order — first match wins per series.
ROLE_PATTERNS: List[tuple] = [
    ("revenue", re.compile(r"revenu|chiffre.?d.affaires|\bsales\b|\bventes\b",
                           re.IGNORECASE)),
    ("capex", re.compile(r"capex|investissement", re.IGNORECASE)),
    # plain "dépenses X" is usually a PARTIAL cost line; claiming it as OPEX
    # overstates margin — require an explicit opex/charges label
    ("opex", re.compile(r"opex|charges?\b", re.IGNORECASE)),
    ("volume", re.compile(r"\bcpo\b|\bffb\b|production|tonnage|\bvolume\b",
                          re.IGNORECASE)),
    ("area", re.compile(r"hectare|\bha\b|surface", re.IGNORECASE)),
]

MAX_BREAKDOWNS = 6


def _tidies(report_sheets) -> List[tuple]:
    """(sheet_name, panel_label, tidy_dict) for every extracted table."""
    out = []
    for s in report_sheets:
        tidy = s.tidy.model_dump() if hasattr(s.tidy, "model_dump") and s.tidy \
            else (s.tidy if isinstance(s.tidy, dict) else None)
        if tidy:
            out.append((s.name, None, tidy))
        for p in s.panels or []:
            t = p.get("tidy")
            if t:
                out.append((s.name, p.get("col_start"), t))
    return out


def _year_charts(report_sheets) -> List[tuple]:
    """(sheet_name, chart) for every year-axis timeseries with full series."""
    out = []
    for name, _, tidy in _tidies(report_sheets):
        ch = (tidy.get("summary") or {}).get("chart")
        if ch and ch.get("kind") == "timeseries" and ch.get("axis") == "year" \
                and ch.get("series_all"):
            out.append((name, ch))
    return out


def _tag_roles(chart: dict) -> Dict[str, dict]:
    """role -> best matching series (largest |total|) within one chart."""
    roles: Dict[str, dict] = {}
    for s in chart["series_all"]:
        label = s["label"]
        for role, rx in ROLE_PATTERNS:
            if rx.search(label):
                total = sum(abs(v) for v in s["values"] if v is not None)
                if role not in roles or total > roles[role]["_total"]:
                    roles[role] = {"label": label, "values": s["values"],
                                   "_total": total}
                break
    return roles


def build_model(report_sheets) -> Optional[Dict[str, Any]]:
    """Assemble the business model, or ``None`` when nothing role-tags."""
    charts = _year_charts(report_sheets)
    if not charts:
        return None

    # spine = the year chart where the most roles were found
    best_sheet, best_roles, best_chart = None, {}, None
    for name, ch in charts:
        roles = _tag_roles(ch)
        if len(roles) > len(best_roles):
            best_sheet, best_roles, best_chart = name, roles, ch
    if not best_roles:
        return None

    periods = [str(p) for p in best_chart["periods"]]

    # supplement missing roles from other year charts with IDENTICAL periods
    for name, ch in charts:
        if ch is best_chart:
            continue
        if [str(p) for p in ch["periods"]] != periods:
            continue
        for role, series in _tag_roles(ch).items():
            if role not in best_roles:
                series["_sheet"] = name
                best_roles[role] = series

    metrics = {
        role: {"label": s["label"], "sheet": s.get("_sheet", best_sheet),
               "values": s["values"]}
        for role, s in best_roles.items()
    }

    # derived metrics — only where both inputs exist, cell by cell
    def _pair(a, b, fn):
        return [round(fn(x, y), 4) if x is not None and y is not None and y != 0
                else None for x, y in zip(a, b)]

    derived: Dict[str, Any] = {}
    rev = metrics.get("revenue", {}).get("values")
    opx = metrics.get("opex", {}).get("values")
    vol = metrics.get("volume", {}).get("values")
    if rev and opx:
        derived["margin"] = [round(r - o, 4) if r is not None and o is not None
                             else None for r, o in zip(rev, opx)]
        derived["margin_pct"] = _pair(derived["margin"], rev,
                                      lambda m, r: m / r * 100)
    if opx and vol:
        derived["opex_per_volume"] = _pair(opx, vol, lambda o, v: o / v)
    if rev and vol:
        derived["revenue_per_volume"] = _pair(rev, vol, lambda r, v: r / v)

    # breakdowns: the top line items already computed at extraction
    breakdowns = []
    for name, panel, tidy in _tidies(report_sheets):
        bd = (tidy.get("summary") or {}).get("breakdown")
        if bd:
            breakdowns.append({"sheet": name, **bd})
    breakdowns.sort(key=lambda b: -sum(abs(i["value"]) for i in b["items"]))

    return {
        "periods": periods,
        "source_sheet": best_sheet,
        "metrics": metrics,
        "derived": derived,
        "breakdowns": breakdowns[:MAX_BREAKDOWNS],
        # scenario/simulation math needs at least revenue + a cost line
        "scenario_ready": bool(rev and opx),
    }
