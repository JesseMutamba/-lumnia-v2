"""Config-first mapping: pin WHICH series carries each business role.

Label heuristics (model.py) are probabilistic; a client mapping is
deterministic. The catch is that a wrong mapping produces a confident wrong
dashboard — so no mapping is trusted until the identities the file itself
carries are re-derived and pass:

* ``margin_identity``   — revenue − opex must equal a mapped margin series
* ``revenue_identity``  — price × volume must equal a mapped revenue series
* ``budget_scale``      — a budget line must be the same order of magnitude
                          as the actual costs it budgets (guards swaps)

A mapping with no checkable identity is honest about it (``ok: None``): the
analyst may pin it manually, but it is never inherited automatically.

Everything here is pure: plain report dicts in, model dicts out.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .model import derive_metrics

# roles a mapping may pin. "margin" maps a witness series used ONLY for
# reconciliation — the shown margin stays derived from revenue − opex.
MAPPABLE_ROLES = ("revenue", "opex", "capex", "budget", "volume",
                  "volume_secondary", "area", "headcount", "price", "margin")

REL_TOLERANCE = 0.01          # identities must hold within 1% (rounding room)


def year_series(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every series of every year-axis chart in a (stored) report dict:
    [{sheet, label, periods, values}] — the mapping's address space."""
    out: List[Dict[str, Any]] = []
    for s in report.get("sheets", []):
        tidies = [s.get("tidy")] + [p.get("tidy") for p in s.get("panels") or []]
        for tidy in tidies:
            if not tidy:
                continue
            ch = (tidy.get("summary") or {}).get("chart")
            if not (ch and ch.get("kind") == "timeseries"
                    and ch.get("axis") == "year" and ch.get("series_all")):
                continue
            periods = [str(p) for p in ch["periods"]]
            for se in ch["series_all"]:
                out.append({"sheet": s.get("name"), "label": se["label"],
                            "periods": periods, "values": se["values"]})
    return out


def resolve(mapping: Dict[str, Dict[str, str]],
            report: Dict[str, Any]
            ) -> Tuple[Optional[Dict[str, dict]], List[str]]:
    """Match every mapped role to one concrete series. Label must match
    exactly; the sheet disambiguates when the same label exists twice.
    Returns (resolved, errors) — resolved is None when anything failed."""
    avail = year_series(report)
    resolved: Dict[str, dict] = {}
    errors: List[str] = []
    for role, ref in mapping.items():
        if role not in MAPPABLE_ROLES:
            errors.append(f"unknown role '{role}'")
            continue
        label = (ref or {}).get("label")
        sheet = (ref or {}).get("sheet")
        hits = [a for a in avail if a["label"] == label
                and (sheet is None or a["sheet"] == sheet)]
        if not hits and sheet is not None:
            # sheet names drift between exports (a CSV's stem IS the month);
            # the label is the stable identity — accept it when unambiguous
            hits = [a for a in avail if a["label"] == label]
            if len(hits) > 1:
                hits = []
        if not hits:
            errors.append(f"{role}: no series labelled '{label}'"
                          + (f" on sheet '{sheet}'" if sheet else ""))
        elif len(hits) > 1:
            errors.append(f"{role}: '{label}' is ambiguous "
                          f"({len(hits)} sheets) — specify the sheet")
        else:
            resolved[role] = hits[0]
    if errors:
        return None, errors
    periods_seen = {tuple(r["periods"]) for r in resolved.values()}
    if len(periods_seen) > 1:
        return None, ["mapped series live on different period axes"]
    return resolved, []


def _rel_delta(a: List, b: List) -> Optional[float]:
    """Worst relative gap between two series, over cells both carry."""
    worst = None
    for x, y in zip(a, b):
        if x is None or y is None:
            continue
        scale = max(abs(x), abs(y))
        if scale == 0:
            continue
        d = abs(x - y) / scale
        worst = d if worst is None else max(worst, d)
    return worst


def reconcile(resolved: Dict[str, dict]) -> Dict[str, Any]:
    """Re-derive every identity the mapped series make checkable.
    ``ok`` is True/False when at least one identity ran, None when the file
    carries no witness — honesty over false confidence."""
    checks: List[Dict[str, Any]] = []

    rev = resolved.get("revenue", {}).get("values")
    opx = resolved.get("opex", {}).get("values")
    mar = resolved.get("margin", {}).get("values")
    if rev and opx and mar:
        derived = [r - o if r is not None and o is not None else None
                   for r, o in zip(rev, opx)]
        d = _rel_delta(derived, mar)
        if d is not None:
            checks.append({"name": "margin_identity",
                           "detail": "revenue − opex = margin",
                           "max_rel_delta": round(d, 6),
                           "ok": d <= REL_TOLERANCE})

    price = resolved.get("price", {}).get("values")
    vol = resolved.get("volume", {}).get("values")
    if rev and price and vol:
        derived = [p * v if p is not None and v is not None else None
                   for p, v in zip(price, vol)]
        d = _rel_delta(derived, rev)
        if d is not None:
            checks.append({"name": "revenue_identity",
                           "detail": "price × volume = revenue",
                           "max_rel_delta": round(d, 6),
                           # unit mismatches (kg vs T) are common: allow 2%
                           "ok": d <= 0.02})

    bud = resolved.get("budget", {}).get("values")
    if opx and bud:
        d = _rel_delta(opx, bud)
        if d is not None:
            checks.append({"name": "budget_scale",
                           "detail": "budget within 60% of actual costs",
                           "max_rel_delta": round(d, 6),
                           "ok": d <= 0.6})

    ran = [c for c in checks if c["ok"] is not None]
    return {"checks": checks,
            "ok": all(c["ok"] for c in ran) if ran else None}


def build_mapped_model(report: Dict[str, Any],
                       resolved: Dict[str, dict]) -> Dict[str, Any]:
    """The business model with roles pinned by the mapping — same derived
    arithmetic as the heuristic path, same shape for every consumer."""
    metrics = {
        role: {"label": r["label"], "sheet": r["sheet"], "values": r["values"]}
        for role, r in resolved.items() if role != "margin"
    }
    any_ref = next(iter(resolved.values()))
    # keep the heuristic model's breakdowns if it produced any
    old = report.get("model") or {}
    return {
        "periods": any_ref["periods"],
        "source_sheet": any_ref["sheet"],
        "metrics": metrics,
        "derived": derive_metrics(metrics),
        "breakdowns": old.get("breakdowns", []),
        "scenario_ready": bool(metrics.get("revenue")),
    }
