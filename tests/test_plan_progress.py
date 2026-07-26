"""Oracle: progress against the projection plan — part-year actuals vs the
plan year, phasing DECLARED, scale-gated, journal-backed for spend.

The anchor engagement: a Q1 actuals workbook against a multi-year plan.
The block answers "how far into the plan year are we, and at what pace" —
and says which phasing rule produced the expectation. What it cannot
support honestly (unit mismatch, missing year, no data) is a named gap.
"""
from __future__ import annotations

from app.pipeline.model import attach_plan_progress

YEAR_MODEL = {
    "periods": ["2025", "2026", "2027"],
    "source_sheet": "PLAN · RECAP",
    "metrics": {
        "opex": {"label": "SOUS-TOTAL OPEX", "sheet": "PLAN · RECAP",
                 "values": [300000, 457300, 641557],
                 "cells": [["C9"], ["D9"], ["E9"]]},
        "capex": {"label": "SOUS-TOTAL CAPEX", "sheet": "PLAN · RECAP",
                  "values": [500000, 870110, 638180]},
        "volume_secondary": {"label": "PRODUCTION CPO", "sheet": "PLAN · RECAP",
                             "values": [500, 2100, 3200]},
    },
    "derived": {},
    "monthly": {
        "periods": [f"2026-{m:02d}-01" for m in range(1, 4)],
        "source_sheet": "EXTRACTION MOIS",
        "metrics": {
            # tonnage actuals, same unit as the plan year: series source
            "volume_secondary": {"label": "CPO Produits (T)",
                                 "sheet": "EXTRACTION MOIS",
                                 "values": [120, 150, 180]},
            # CDF-scale spend against a USD plan: scale-gated OUT
            "opex": {"label": "Total PO / P30g", "sheet": "EXTRACTION MOIS",
                     "values": [140e6, 150e6, 160e6]},
        },
        "derived": {},
    },
}

# the plan pool carries a monthly CPO plan for 2026 -> phased expectation
PLAN_CHARTS = [("PLAN · PRODUCTION", {
    "kind": "timeseries", "axis": "date",
    "periods": [f"2026-{m:02d}-01" for m in range(1, 13)],
    "series_all": [{"label": "CPO Produits (T) prévu",
                    "values": [87, 235, 336, 470, 336, 201,
                               110, 60, 90, 75, 50, 50]}],
})]

JOURNAL = {"exec": {
    "months": ["2026-01", "2026-02", "2026-03"],
    "total_out_usd": 326389.85,
    "destinations": [
        {"kind": "opex", "usd": 171824.85, "pct": 52.6},
        {"kind": "dgo", "usd": 149852.11, "pct": 45.9},
        {"kind": "capex", "usd": 4712.89, "pct": 1.4},
    ],
}}


def _model():
    import copy
    return copy.deepcopy(YEAR_MODEL)


def test_series_progress_with_monthly_plan_phasing():
    m = _model()
    attach_plan_progress(m, {"exec": {}}, plan_charts=PLAN_CHARTS)
    pp = m["plan_progress"]
    assert pp["year"] == "2026"
    v = pp["roles"]["volume_secondary"]
    assert v["source"] == "series"
    assert v["actual_to_date"] == 450                # 120+150+180
    assert v["plan_year"] == 2100
    assert v["pct_of_year"] == round(450 / 2100 * 100, 1)
    # phased: the monthly plan says Q1 = (87+235+336) / 2100 of the year
    assert v["phasing"] == "monthly_plan"
    assert v["expected_pct"] == round((87 + 235 + 336) / 2100 * 100, 1)
    assert v["n_months"] == 3 and v["window"] == ["2026-01-01", "2026-03-01"]


def test_scale_gate_falls_back_to_journal_for_spend():
    m = _model()
    attach_plan_progress(m, JOURNAL, plan_charts=[])
    pp = m["plan_progress"]
    o = pp["roles"]["opex"]
    # the CDF series failed the gate; the journal's verified USD carried it
    assert o["source"] == "journal"
    assert o["actual_to_date"] == 171824.85
    assert o["pct_of_year"] == round(171824.85 / 457300 * 100, 1)
    assert o["phasing"] == "linear" and o["expected_pct"] == 25.0
    c = pp["roles"]["capex"]
    assert c["source"] == "journal"
    assert c["pct_of_year"] == round(4712.89 / 870110 * 100, 1)


def test_unsupported_roles_are_named_gaps():
    m = _model()
    m["monthly"]["metrics"].pop("volume_secondary")
    attach_plan_progress(m, {"exec": {}}, plan_charts=[])
    pp = m["plan_progress"]
    assert "volume_secondary" not in pp["roles"]
    reasons = {g["role"]: g["reason"] for g in pp["gaps"]}
    assert "volume_secondary" in reasons          # nothing measures it
    assert "opex" in reasons                      # gate refused, no journal
    assert "magnitude" in reasons["opex"]


def test_no_plan_shaped_spine_no_block():
    m = _model()
    m["source_sheet"] = "HISTORIQUE"              # not budget-shaped
    attach_plan_progress(m, JOURNAL, plan_charts=[])
    assert "plan_progress" not in m


def test_journal_year_must_match_the_plan_axis():
    m = _model()
    j = {"exec": {**JOURNAL["exec"], "months": ["2031-01", "2031-02",
                                                "2031-03"]}}
    m["monthly"]["metrics"].pop("opex")
    m["monthly"]["periods"] = []
    m["monthly"]["metrics"].pop("volume_secondary")
    attach_plan_progress(m, j, plan_charts=[])
    pp = m.get("plan_progress")
    assert not pp or "opex" not in (pp.get("roles") or {})
