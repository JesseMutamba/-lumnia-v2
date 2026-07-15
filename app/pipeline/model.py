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

# Role patterns, checked in order — first match wins per series. Order is
# deliberate: "price" sits before "revenue" so "Prix de vente" isn't claimed
# as revenue, and "capex"/"opex" sit before "volume" so "Coûts de production"
# isn't claimed as volume.
ROLE_PATTERNS: List[tuple] = [
    ("price", re.compile(r"\bprix\b|\bprice\b|tarif", re.IGNORECASE)),
    ("revenue", re.compile(
        r"revenu|chiffre.?d.affaires|recettes?\b|produits?\s+d.exploitation"
        r"|\bsales\b|\bventes?\b|\bincome\b|turnover", re.IGNORECASE)),
    ("capex", re.compile(r"capex|investissement|immobilisations?",
                         re.IGNORECASE)),
    # a budgeted/forecast line is a TARGET, not a cost — tagged before opex
    # so "Budget OPEX" never competes with the actual OPEX series
    ("budget", re.compile(r"budget|pr[ée]vision(nel)?s?\b|pr[ée]vus?\b"
                          r"|forecast", re.IGNORECASE)),
    # plain "dépenses X" is usually a PARTIAL cost line; claiming it as OPEX
    # overstates margin — require an explicit opex/charges/coûts-style label
    ("opex", re.compile(
        r"opex|charges?\b"
        r"|co[uû]ts?\s+(d.)?(op[ée]rat\w*|exploitation|production|fonctionnement)"
        r"|operating\s+(costs?|expenses?)"
        r"|frais\s+(g[ée]n[ée]raux|de\s+fonctionnement)", re.IGNORECASE)),
    ("volume", re.compile(
        # (?<![a-z0-9])cpo… : \b fails across "_" (CPO_Produced), so use
        # lookarounds that treat underscores as boundaries too
        r"(?<![a-z0-9])(?:cpo|ffb)(?![a-z0-9])|production|tonnage|\bvolume\b"
        r"|quantit[ée]s?|r[ée]coltes?\b|harvest|\boutput\b"
        r"|unit[ée]s?\s+(vendues|produites)|units\s+sold", re.IGNORECASE)),
    ("area", re.compile(r"hectare|\bha\b|surface|superficie|acres?\b|m²",
                        re.IGNORECASE)),
    ("headcount", re.compile(
        r"effectifs?\b|employ[ée]s?\b|salari[ée]s?\b|personnel\b|headcount"
        r"|\bstaff\b", re.IGNORECASE)),
]

MAX_BREAKDOWNS = 6

# TOTAL/CUMUL series are aggregates of the others — never a role series
# (same rule as build_matrix_semantics, which imports its copy from here)
DERIVED_SERIES_RX = re.compile(r"total|cumul|sous.?tot|grand.?tot",
                               re.IGNORECASE)

# A per-unit RATE row ("Cost / tonne CPO", "Prix par kg") is a derived
# indicator, not a level series — tagging one as revenue/opex/volume corrupts
# every downstream division (a $/t row claimed as volume once out-tagged the
# real tonnage rows on sheer dollar magnitude). Only the `price` role is
# legitimately per-unit.
RATE_SERIES_RX = re.compile(
    r"(/|\bper\b|\bpar\b)\s*\(?\s*"
    r"(t|tonnes?|kg|km|l|unit[ée]?s?|ha|hectares?|m²|jour|day|head|t[êe]te)s?\b",
    re.IGNORECASE)

# The actual-vs-budget comparison only renders when the year spine really IS
# a plan — an honest "vs budget" claim needs a budget-shaped source, not any
# historical year series that happens to share the axis.
BUDGET_SHEET_RX = re.compile(r"budget|pr[ée]vision(nel)?s?|forecast|\bplan\b",
                             re.IGNORECASE)


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
    """role -> best matching series (largest |total|) within one chart.

    When TWO distinct series role-tag as volume (e.g. FFB harvested and CPO
    produced), the runner-up is kept as ``volume_secondary`` so the model can
    derive a conversion ratio between them.
    """
    matches: Dict[str, List[dict]] = {}
    for s in chart["series_all"]:
        label = s["label"]
        for role, rx in ROLE_PATTERNS:
            if role != "price" and RATE_SERIES_RX.search(str(label)):
                continue                 # a rate row is no level series
            if rx.search(label):
                total = sum(abs(v) for v in s["values"] if v is not None)
                matches.setdefault(role, []).append(
                    {"label": label, "values": s["values"], "_total": total,
                     "_derived": bool(DERIVED_SERIES_RX.search(str(label)))})
                break
    roles: Dict[str, dict] = {}
    for role, cands in matches.items():
        # a TOTAL/CUMUL line only carries a role when no clean series
        # claims it — "SOUS-TOTAL CAPEX" as the lone capex line is the
        # honest total; "TOTAL PRODUCTION" beside real production rows
        # is an aggregate that would double-count
        clean = [c for c in cands if not c["_derived"]]
        pick = sorted(clean or cands, key=lambda c: -c["_total"])
        roles[role] = pick[0]
        if role == "volume" and len(pick) > 1 and pick[1]["_total"] > 0:
            roles["volume_secondary"] = pick[1]
    return roles


def derive_metrics(metrics: Dict[str, dict]) -> Dict[str, Any]:
    """Derived indicators — only where both inputs exist, cell by cell.
    Shared by the heuristic model and the mapping path so a mapped model
    can never disagree with a detected one about the arithmetic."""
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
    area = metrics.get("area", {}).get("values")
    if rev and area:
        derived["revenue_per_area"] = _pair(rev, area, lambda r, a: r / a)
    if opx and area:
        derived["opex_per_area"] = _pair(opx, area, lambda o, a: o / a)
    # two volume series -> conversion ratio (secondary/primary, e.g. an
    # extraction rate: tonnes of product out per tonne of raw input)
    vol2 = metrics.get("volume_secondary", {}).get("values")
    if vol and vol2:
        derived["volume_ratio"] = _pair(vol2, vol, lambda b, a: b / a)
        # cost per tonne of OUTPUT (e.g. OPEX/T CPO) — the industry lens
        # when both input and output volumes exist
        if opx:
            derived["opex_per_volume_out"] = _pair(opx, vol2,
                                                   lambda o, v: o / v)
    # actual vs budgeted cost, period by period (+% = over budget)
    bud = metrics.get("budget", {}).get("values")
    if opx and bud:
        derived["opex_budget_variance_pct"] = _pair(
            opx, bud, lambda o, b: (o - b) / b * 100)
    return derived


def _unit_cost_budget(periods: List[str], year_derived: Dict[str, Any],
                      monthly: Dict[str, Any],
                      spine_sheet: Optional[str]) -> Optional[Dict[str, Any]]:
    """The "$/t actual vs budget" comparison, computed HERE so the frontend
    only formats: the budget-year unit cost for the actuals' year is the
    target, and each month carries its variance. None unless the year spine
    is a budget-shaped sheet AND both sides derive the same unit-cost basis."""
    if not spine_sheet or not BUDGET_SHEET_RX.search(str(spine_sheet)):
        return None
    mo_d = monthly.get("derived") or {}
    basis = next((b for b in ("opex_per_volume_out", "opex_per_volume")
                  if mo_d.get(b) and year_derived.get(b)), None)
    if basis is None:
        return None
    years = [p[:4] for p in monthly.get("periods", []) if len(p) >= 4]
    if not years or len(set(years)) != 1 or years[0] not in periods:
        return None                    # actuals must sit inside ONE plan year
    target = year_derived[basis][periods.index(years[0])]
    if target is None or target == 0:
        return None
    actual = mo_d[basis]
    return {
        "basis": basis,
        "target": target,
        "target_period": years[0],
        "variance_pct": [round((a - target) / target * 100, 1)
                         if a is not None else None for a in actual],
    }


def _monthly_block(report_sheets) -> Optional[Dict[str, Any]]:
    """Actuals: the date-axis chart where the most roles tag — volumes in,
    volumes out, per period — with the same derived arithmetic (the monthly
    conversion ratio IS the extraction rate). None when nothing tags."""
    best = None
    for name, _, tidy in _tidies(report_sheets):
        ch = (tidy.get("summary") or {}).get("chart")
        if not (ch and ch.get("kind") == "timeseries"
                and ch.get("axis") != "year" and ch.get("series_all")):
            continue
        roles = _tag_roles(ch)
        if roles and (best is None or len(roles) > len(best[2])):
            best = (name, ch, roles)
    if best is None:
        return None
    name, ch, roles = best
    metrics = {
        role: {"label": s["label"], "sheet": name, "values": s["values"]}
        for role, s in roles.items()
    }
    return {"periods": [str(p) for p in ch["periods"]],
            "source_sheet": name,
            "metrics": metrics,
            "derived": derive_metrics(metrics)}


def build_model(report_sheets) -> Optional[Dict[str, Any]]:
    """Assemble the business model, or ``None`` when nothing role-tags."""
    charts = _year_charts(report_sheets)
    monthly = _monthly_block(report_sheets)
    if not charts:
        if monthly is None:
            return None
        # actuals with no projections: the Production view still deserves
        # a model — there is just nothing to simulate
        return {"periods": [], "source_sheet": None, "metrics": {},
                "derived": {}, "breakdowns": [], "scenario_ready": False,
                "monthly": monthly}

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
    derived = derive_metrics(metrics)

    if monthly:
        ucb = _unit_cost_budget(periods, derived, monthly, best_sheet)
        if ucb:
            monthly["unit_cost_budget"] = ucb

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
        # scenario/simulation math needs a revenue line; cost levers appear
        # only when a cost series was actually found
        "scenario_ready": bool(metrics.get("revenue")),
        "monthly": monthly,
    }
