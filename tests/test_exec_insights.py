"""Oracle: the portfolio-sample insights, computed in the model.

net_cash (margin − investment) and its cumulative walk live on the plan
years; the first cumulative crossing into positive is the cash-positive
period and the deepest cumulative deficit is the capital bridge. The
monthly block gains a cash split (invested vs operating share of tracked
spend). Every gate is honest: no capex row → no net-cash story; no
monthly opex+capex pair → no split.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _book(with_capex_plan: bool = True,
          capex_plan: tuple = (500000, 300000, 100000)) -> bytes:
    actuals = [
        ["Métrique", "Jan 2026", "Fév 2026", "Mar 2026"],
        ["Production CPO (t)", 16, 17.5, 18.5],
        ["Production FFB (t)", 76, 80, 84],
        ["OPEX — coût de production (USD)", 14200, 16900, 15400],
        ["CAPEX — investissement (USD)", 41000, 52500, 49800],
    ]
    budget = [
        ["Métrique", 2025, 2026, 2027],
        ["Production FFB (t)", 3000, 3200, 4500],
        ["Production CPO (t)", 650, 700, 990],
        ["OPEX — coût de production (USD)", 351000, 378000, 534600],
        ["Revenus — bruts (USD)", 682500, 735000, 1039500],
    ]
    if with_capex_plan:
        budget.append(["CAPEX — investissement (USD)", *capex_plan])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(actuals).to_excel(w, sheet_name="Réalisé", header=False,
                                       index=False)
        pd.DataFrame(budget).to_excel(w, sheet_name="Budget", header=False,
                                      index=False)
    return buf.getvalue()


def _model(book: bytes, salt: bytes) -> dict:
    r = client.post("/analyze", files={
        "file": ("plan.xlsx", book + salt, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["model"]


def test_net_cash_walk_and_insights():
    m = _model(_book(), b"walk")
    d = m["derived"]
    # margin 331500/357000/504900 minus capex 500000/300000/100000
    assert d["net_cash"] == [-168500, 57000, 404900]
    assert d["net_cash_cum"] == [-168500, -111500, 293400]
    ins = m["insights"]
    assert ins["cash_positive_period"] == "2027"   # first cum >= 0
    assert ins["capital_bridge"] == 168500         # deepest deficit


def test_monthly_cash_split_is_exact():
    m = _model(_book(), b"split")
    cash = m["monthly"]["cash"]
    assert cash["capex_total"] == 143300
    assert cash["opex_total"] == 46500
    assert cash["total"] == 189800
    assert cash["invested_pct"] == 75.5            # 143300/189800, 1 decimal


def test_no_capex_plan_means_no_net_cash_story():
    m = _model(_book(with_capex_plan=False), b"gate")
    assert "net_cash" not in m["derived"]
    assert "cash_positive_period" not in (m.get("insights") or {})
    # the monthly split is untouched by the plan-side gate
    assert m["monthly"]["cash"]["invested_pct"] == 75.5


def test_plan_never_negative_needs_no_bridge():
    # a plan that is cash-positive from year one has no bridge to narrate
    m = _model(_book(capex_plan=(100000, 100000, 100000)), b"nobridge")
    d = m["derived"]
    assert d["net_cash"][0] > 0
    ins = m.get("insights") or {}
    assert "capital_bridge" not in ins
    assert "cash_positive_period" not in ins
