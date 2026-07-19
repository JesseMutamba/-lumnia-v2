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

# a series from another sheet joins the spine only when at least this many
# aligned periods carry values — fewer is an honest gap, never a metric
MIN_XSHEET_OVERLAP = 3

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
# historical year series that happens to share the axis. The same shape marks
# a MONTHLY sheet as a plan pool: compared to actuals, never merged into them.
BUDGET_SHEET_RX = re.compile(
    r"budget|pr[ée]vision(nel)?s?|forecast|\bplan\b|projections?",
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


def _tag_roles(chart: dict, plan: bool = False) -> Dict[str, dict]:
    """role -> best matching series (largest |total|) within one chart.

    When TWO distinct series role-tag as volume (e.g. FFB harvested and CPO
    produced), the runner-up is kept as ``volume_secondary`` so the model can
    derive a conversion ratio between them.

    ``plan=True`` (a chart from a plan-shaped sheet) skips the generic
    "budget" pattern so a prévu-labeled row falls through to its SUBSTANTIVE
    role — "PRODUCTION PRÉVUE" on a plan sheet is a production plan, not a
    budget line.
    """
    matches: Dict[str, List[dict]] = {}
    for s in chart["series_all"]:
        label = s["label"]
        for role, rx in ROLE_PATTERNS:
            if plan and role == "budget":
                continue
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


def _reindex(values: List, periods: List[str],
             spine_periods: List[str]) -> List:
    """A foreign series re-indexed onto the spine axis by exact period-string
    equality; None where the spine period is absent from the foreign axis.
    The spine axis is authoritative — it never grows or reorders, and no
    positional zip ever crosses differing axes. A period string appearing
    MORE THAN ONCE in the foreign axis is unknowable — which column is
    truth? — so it joins as None rather than silently last-wins."""
    counts: Dict[str, int] = {}
    for p in periods:
        counts[p] = counts.get(p, 0) + 1
    by_p = {p: v for p, v in zip(periods, values) if counts[p] == 1}
    return [by_p.get(p) for p in spine_periods]


def _dropped_outside(values: List, periods: List[str],
                     spine_periods: List[str]) -> int:
    """Non-empty foreign cells whose period falls outside the spine axis —
    excluded from the join, and DECLARED so no total quietly understates
    what the workbook holds."""
    spine = set(spine_periods)
    return sum(1 for p, v in zip(periods, values)
               if v is not None and p not in spine)


def _pair_volumes(roles: Dict[str, dict], entry: dict,
                  gaps: List[Dict[str, str]], grain: str) -> None:
    """A volume series from another sheet against the spine's own: the larger
    over the aligned cells is the raw input (e.g. FFB) and takes ``volume``,
    the smaller (e.g. CPO) becomes ``volume_secondary`` — keeping
    volume_ratio = output/input, an extraction rate below 1, the same
    largest-first convention as _tag_roles."""
    if "volume_secondary" in roles:
        return                        # a pair already exists — first wins
    spine_vol = roles["volume"]
    if str(entry["label"]).strip() == str(spine_vol["label"]).strip():
        return                        # the same line copied across sheets
    both = [(a, b) for a, b in zip(spine_vol["values"], entry["values"])
            if a is not None and b is not None]
    if len(both) < MIN_XSHEET_OVERLAP:
        gaps.append({
            "metric": "volume_ratio",
            "grain": grain,
            "reason": "volume series found on two sheets but fewer than "
                      f"{MIN_XSHEET_OVERLAP} shared periods carry values",
            "requires": f"{MIN_XSHEET_OVERLAP}+ overlapping periods "
                        "across sheets"})
        return
    if sum(abs(b) for _, b in both) > sum(abs(a) for a, _ in both):
        roles["volume_secondary"] = spine_vol      # swap: keeps its own sheet
        roles["volume"] = entry
    else:
        roles["volume_secondary"] = entry


def _supplement(spine_periods: List[str], roles: Dict[str, dict],
                others: List[tuple], grain: str,
                plan: bool = False) -> List[Dict[str, str]]:
    """Fill roles the spine chart lacks from OTHER charts of the same grain,
    re-indexed onto the spine axis. A second volume series found on another
    sheet becomes the conversion pair (volume_secondary) via _pair_volumes;
    a sheet's OWN pair travels together (its secondary rides along when its
    primary took the spine's volume slot). An identical axis keeps the old
    unconditional admission — the >=3 overlap gate exists for genuinely
    cross-axis joins. Returns the alignment gaps of THIS grain only; the
    completed-pair wipe never reaches another grain's gaps."""
    sgaps: List[Dict[str, str]] = []
    for name, ch in others:
        ch_periods = [str(p) for p in ch["periods"]]
        identical = ch_periods == spine_periods
        # _tag_roles inserts volume before volume_secondary, so a sheet's
        # primary always lands before its secondary rides along
        for role, series in _tag_roles(ch, plan=plan).items():
            vals = series["values"] if identical \
                else _reindex(series["values"], ch_periods, spine_periods)
            entry = {**series, "values": vals, "_sheet": name}
            dropped = _dropped_outside(series["values"], ch_periods,
                                       spine_periods)
            if dropped:
                entry["_dropped_outside"] = dropped
            if role == "volume" and "volume" in roles:
                _pair_volumes(roles, entry, sgaps, grain)
                continue
            if role == "volume_secondary":
                if ("volume_secondary" not in roles
                        and roles.get("volume", {}).get("_sheet") == name):
                    roles["volume_secondary"] = entry
                continue
            if role in roles:
                continue
            if not identical and sum(
                    v is not None for v in vals) < MIN_XSHEET_OVERLAP:
                continue
            roles[role] = entry
    if "volume_secondary" in roles:
        # a later sheet completed this grain's pair — its gaps are moot
        sgaps = [g for g in sgaps if g["metric"] != "volume_ratio"]
    return sgaps


def _dedupe_gaps(gaps: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen, out = set(), []
    for g in gaps:
        key = (g["metric"], g.get("grain"))
        if key not in seen:
            seen.add(key)
            out.append(g)
    return out


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
    # net cash after investment + its cumulative walk — the path-to-profit
    # spine. The cumulative is honest: it goes None from the first gap on,
    # a partial walk would claim a balance nobody computed.
    cpx = metrics.get("capex", {}).get("values")
    if "margin" in derived and cpx:
        net = _pair(derived["margin"], cpx, lambda mg, cx: mg - cx)
        if any(v is not None for v in net):
            derived["net_cash"] = net
            cum, run = [], 0.0
            for v in net:
                if v is None or run is None:
                    run = None
                    cum.append(None)
                else:
                    run += v
                    cum.append(round(run, 4))
            derived["net_cash_cum"] = cum
    return derived


def _cash_insights(periods: List[str],
                   derived: Dict[str, Any]) -> Dict[str, Any]:
    """Plan-level cash story: the first period the cumulative walk turns
    positive, and the deepest deficit crossed to get there (the capital
    bridge). Both only exist when the plan actually starts in the red —
    an always-positive plan has no bridge to narrate."""
    ins: Dict[str, Any] = {}
    cum = derived.get("net_cash_cum")
    if not cum:
        return ins
    known = [(p, v) for p, v in zip(periods, cum) if v is not None]
    if not known or known[0][1] >= 0:
        return ins
    deepest = min(v for _, v in known)
    if deepest < 0:
        ins["capital_bridge"] = round(-deepest, 4)
    for p, v in known:
        if v >= 0:
            ins["cash_positive_period"] = p
            break
    return ins


def _cash_split(monthly: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Invested vs operating share of the tracked monthly spend — only when
    both an opex and a capex series exist and money actually moved."""
    met = monthly.get("metrics") or {}
    opx = (met.get("opex") or {}).get("values")
    cpx = (met.get("capex") or {}).get("values")
    if not opx or not cpx:
        return None
    # apples to apples: cross-sheet series may cover different windows, so
    # the invested share only counts periods where BOTH sides are tracked
    both = [(o, c) for o, c in zip(opx, cpx)
            if o is not None and c is not None]
    if not both:
        return None
    opex_total = sum(o for o, _ in both)
    capex_total = sum(c for _, c in both)
    total = opex_total + capex_total
    if total <= 0:
        return None
    return {"opex_total": round(opex_total, 4),
            "capex_total": round(capex_total, 4),
            "total": round(total, 4),
            "invested_pct": round(capex_total / total * 100, 1)}


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


def _monthly_block(report_sheets,
                   gaps: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Actuals: the date-axis chart where the most roles tag — volumes in,
    volumes out, per period — with the same derived arithmetic (the monthly
    conversion ratio IS the extraction rate). Other date-axis charts
    supplement missing roles across sheets, joined on the spine axis.

    Charts from plan-shaped sheets (BUDGET/PLAN/PREVISIONS/PROJECTIONS) are
    a SEPARATE pool: plan series are compared to the actuals, never merged
    into them — a plan number presented as an actual is a fabrication.
    None when nothing tags."""
    charts, plan_charts = [], []
    for name, _, tidy in _tidies(report_sheets):
        ch = (tidy.get("summary") or {}).get("chart")
        if (ch and ch.get("kind") == "timeseries"
                and ch.get("axis") != "year" and ch.get("series_all")):
            if BUDGET_SHEET_RX.search(str(name)):
                plan_charts.append((name, ch))
            else:
                charts.append((name, ch))
    best = None
    for name, ch in charts:
        roles = _tag_roles(ch)
        if roles and (best is None or len(roles) > len(best[2])):
            best = (name, ch, roles)
    if best is None:
        return None
    name, ch, roles = best
    periods = [str(p) for p in ch["periods"]]
    gaps.extend(_supplement(periods, roles,
                            [(n, c) for n, c in charts if c is not ch],
                            "monthly"))
    metrics = _public_metrics(roles, name)
    mo = {"periods": periods,
          "source_sheet": name,
          "metrics": metrics,
          "derived": derive_metrics(metrics)}
    pva = _plan_vs_actual(mo, plan_charts, gaps)
    if pva:
        mo["plan_vs_actual"] = pva
        # the plan pool's OWN axis, uncut: plan_vs_actual re-indexes the
        # plan onto the actuals window, but the outlook story (rest-of-year
        # plan vs the run-rate so far) needs the months the window drops
        plan = _plan_block(plan_charts)
        if plan is not None:
            mo["plan"] = {
                "periods": plan["periods"],
                "source_sheet": plan["source_sheet"],
                "metrics": {role: {"label": m["label"],
                                   "values": m["values"]}
                            for role, m in plan["metrics"].items()},
            }
    return mo


def _plan_block(plan_charts: List[tuple]) -> Optional[Dict[str, Any]]:
    """The plan pool folded into one role map, same spine-and-supplement
    shape as the actuals. Plan-internal alignment gaps are not surfaced —
    the plan is a comparison source, not a rendered story."""
    best = None
    for name, ch in plan_charts:
        roles = _tag_roles(ch, plan=True)
        if roles and (best is None or len(roles) > len(best[2])):
            best = (name, ch, roles)
    if best is None:
        return None
    name, ch, roles = best
    periods = [str(p) for p in ch["periods"]]
    _supplement(periods, roles,
                [(n, c) for n, c in plan_charts if c is not ch],
                "monthly", plan=True)
    return {"periods": periods, "source_sheet": name,
            "metrics": _public_metrics(roles, name)}


# plan-flavored words carry no identity: "PRODUCTION CPO PREVUE" and
# "PRODUCTION CPO" describe the same quantity
_PLANISH_TOKEN_RX = re.compile(
    r"^(pr[ée]vu[es]?|pr[ée]vision\w*|plan\w*|budget\w*|forecast\w*"
    r"|projet[ée]?[es]?)$", re.IGNORECASE)


def _sig_tokens(label: Any) -> set:
    """The label's identity tokens: alphabetic runs of 3+ chars, minus the
    plan-flavored words. Deterministic; no stemming, no guessing."""
    toks = re.findall(r"[A-Za-zÀ-ÿ]{3,}", str(label))
    return {t.upper() for t in toks if not _PLANISH_TOKEN_RX.match(t)}


def _match_plan_volume(pm: Dict[str, Any],
                       actual_metrics: Dict[str, dict]) -> Optional[str]:
    """Which ACTUAL volume series does this plan volume series describe?
    Matched by shared label tokens — a CPO plan must meet the CPO actual,
    never the FFB actual that happens to hold the primary volume slot.
    Returns the actual role name, or None when the pairing is unknowable
    (two candidates, no token says which)."""
    cands = [r for r in ("volume", "volume_secondary") if r in actual_metrics]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    pt = _sig_tokens(pm["label"])
    scores = {r: len(pt & _sig_tokens(actual_metrics[r]["label"]))
              for r in cands}
    best = max(scores.values())
    winners = [r for r in cands if scores[r] == best]
    return winners[0] if len(winners) == 1 else None


def _plan_vs_actual(monthly: Dict[str, Any], plan_charts: List[tuple],
                    gaps: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Attainment against the plan, role by role: the plan re-indexed onto
    the ACTUALS axis (exact period-string equality), pct_of_plan cell by
    cell, totals over the common window only. Fewer than 3 aligned pairs
    for every shared role is an honest gap, never a number. Volume plans
    pair by label identity (see _match_plan_volume) — an unknowable
    pairing is a gap, never a guess."""
    plan = _plan_block(plan_charts)
    if plan is None:
        return None
    out: Dict[str, Any] = {}
    misaligned = False
    unmatched = False
    for role, pm in plan["metrics"].items():
        if role in ("volume", "volume_secondary"):
            target = _match_plan_volume(pm, monthly["metrics"])
            if target is None:
                unmatched = "volume" in monthly["metrics"]
                continue
            if target in out:
                continue               # first plan series claimed it
            role = target
        am = monthly["metrics"].get(role)
        if am is None:
            continue                   # a plan with no actual: no claim
        vals = _reindex(pm["values"], plan["periods"], monthly["periods"])
        both = [(a, p) for a, p in zip(am["values"], vals)
                if a is not None and p is not None]
        if len(both) < MIN_XSHEET_OVERLAP:
            misaligned = True
            continue
        plan_total = sum(p for _, p in both)
        actual_total = sum(a for a, _ in both)
        out[role] = {
            "plan": vals,
            "plan_label": pm["label"],
            "plan_sheet": pm["sheet"],
            "pct_of_plan": [round(a / p * 100, 1)
                            if a is not None and p is not None and p != 0
                            else None
                            for a, p in zip(am["values"], vals)],
            "plan_total": round(plan_total, 4),
            "actual_total": round(actual_total, 4),
            "pct_of_plan_total": round(actual_total / plan_total * 100, 1)
            if plan_total else None,
        }
    if not out and (misaligned or unmatched):
        gaps.append({
            "metric": "plan_vs_actual",
            "grain": "monthly",
            "reason": ("a plan sheet was found but fewer than "
                       f"{MIN_XSHEET_OVERLAP} periods align with the actuals")
            if misaligned else
            ("a plan volume series could not be matched to a single "
             "actuals series"),
            "requires": (f"{MIN_XSHEET_OVERLAP}+ overlapping periods "
                         "between plan and actuals")
            if misaligned else
            "plan labels that name the quantity they plan"})
    return out or None


def _public_metrics(roles: Dict[str, dict],
                    spine_sheet: Optional[str]) -> Dict[str, dict]:
    """Strip the private underscore fields; declare out-of-window drops."""
    out: Dict[str, dict] = {}
    for role, s in roles.items():
        m = {"label": s["label"], "sheet": s.get("_sheet", spine_sheet),
             "values": s["values"]}
        if s.get("_dropped_outside"):
            m["dropped_outside_axis"] = s["_dropped_outside"]
        out[role] = m
    return out


def build_model(report_sheets) -> Optional[Dict[str, Any]]:
    """Assemble the business model, or ``None`` when nothing role-tags."""
    gaps: List[Dict[str, str]] = []
    charts = _year_charts(report_sheets)
    monthly = _monthly_block(report_sheets, gaps)
    if not charts:
        if monthly is None:
            return None
        # actuals with no projections: the Production view still deserves
        # a model — there is just nothing to simulate
        out = {"periods": [], "source_sheet": None, "metrics": {},
               "derived": {}, "breakdowns": [], "scenario_ready": False,
               "monthly": monthly}
        if gaps:
            out["gaps"] = _dedupe_gaps(gaps)
        return out

    # spine = the year chart where the most roles were found
    best_sheet, best_roles, best_chart = None, {}, None
    for name, ch in charts:
        roles = _tag_roles(ch)
        if len(roles) > len(best_roles):
            best_sheet, best_roles, best_chart = name, roles, ch
    if not best_roles:
        return None

    periods = [str(p) for p in best_chart["periods"]]

    # supplement missing roles from other year charts, joined onto the spine
    # axis by exact period-string equality (cross-sheet ratio pairs included)
    gaps.extend(_supplement(periods, best_roles,
                            [(n, c) for n, c in charts if c is not best_chart],
                            "yearly"))

    metrics = _public_metrics(best_roles, best_sheet)
    derived = derive_metrics(metrics)

    if monthly:
        ucb = _unit_cost_budget(periods, derived, monthly, best_sheet)
        if ucb:
            monthly["unit_cost_budget"] = ucb
        cash = _cash_split(monthly)
        if cash:
            monthly["cash"] = cash

    # breakdowns: the top line items already computed at extraction
    breakdowns = []
    for name, panel, tidy in _tidies(report_sheets):
        bd = (tidy.get("summary") or {}).get("breakdown")
        if bd:
            breakdowns.append({"sheet": name, **bd})
    breakdowns.sort(key=lambda b: -sum(abs(i["value"]) for i in b["items"]))

    out = {
        "periods": periods,
        "source_sheet": best_sheet,
        "metrics": metrics,
        "derived": derived,
        "insights": _cash_insights(periods, derived),
        "breakdowns": breakdowns[:MAX_BREAKDOWNS],
        # scenario/simulation math needs a revenue line; cost levers appear
        # only when a cost series was actually found
        "scenario_ready": bool(metrics.get("revenue")),
        "monthly": monthly,
    }
    if gaps:
        out["gaps"] = _dedupe_gaps(gaps)
    return out
