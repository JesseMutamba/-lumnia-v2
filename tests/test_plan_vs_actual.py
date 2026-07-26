"""Phase 2: plan vs actual at the monthly grain, within one workbook.

A plan-shaped sheet (BUDGET/PLAN/PREVISIONS/PROJECTIONS in the sheet name)
is a separate pool: its series never merge into the actuals spine — they
are COMPARED to it. The plan joins the actuals axis by exact period-string
equality; fewer than 3 aligned pairs is an honest gap, never a number.
Attainment = actual / plan, cell by cell, totals over the common window.
"""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MONTHS = [dt.date(2025, m, 28) for m in range(7, 13)]
CPO = [10, 40, 50, 100, 80, 16]
PLAN_CPO = [20, 80, 100, 200, 160, 32]        # exactly 2x -> 50% attainment
OPEX = [10, 10, 10, 10, 10, 10]
PLAN_OPEX = [20, 20, 20, 20, 20, 20]


def _book(*sheets: tuple) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets:
            df.to_excel(xw, sheet_name=name, header=False, index=False)
    return buf.getvalue()


def _analyze(content: bytes, name="pvak.xlsx") -> dict:
    return client.post("/analyze", files={
        "file": (name, content, "application/octet-stream")}).json()


def _sheet(banner: str, rows: list, months=MONTHS) -> pd.DataFrame:
    return pd.DataFrame([[banner, *([None] * len(months))],
                         ["SERIE", *months], *rows])


def _actuals(months=MONTHS) -> pd.DataFrame:
    return _sheet("JOURNAL PRODUCTION", [
        ["PRODUCTION CPO (T)", *CPO],
        ["CHARGES OPEX", *OPEX],
    ], months)


def _plan(months=MONTHS) -> pd.DataFrame:
    # prévu-labeled rows on a plan sheet must tag their SUBSTANTIVE role
    # (volume, opex) — not the generic "budget" role
    return _sheet("PLAN OPERATIONNEL", [
        ["PRODUCTION CPO PREVUE (T)", *PLAN_CPO],
        ["OPEX PREVISIONNEL", *PLAN_OPEX],
    ], months)


def test_monthly_attainment_against_the_plan_sheet():
    rep = _analyze(_book(("JOURNAL", _actuals()), ("BUDGET 2025", _plan())))
    mo = rep["model"]["monthly"]
    pva = mo["plan_vs_actual"]
    v = pva["volume"]
    assert v["plan"] == PLAN_CPO
    assert v["plan_label"] == "PRODUCTION CPO PREVUE (T)"
    assert v["plan_sheet"] == "BUDGET 2025"
    assert v["pct_of_plan"] == [50.0] * 6
    assert (v["plan_total"], v["actual_total"]) == (592, 296)
    assert v["pct_of_plan_total"] == 50.0
    o = pva["opex"]
    assert o["pct_of_plan"] == [50.0] * 6
    assert o["pct_of_plan_total"] == 50.0
    assert not rep["model"].get("gaps")


def test_orders_of_magnitude_mismatch_is_a_gap_not_a_number():
    # A cost line in CDF paired against a tonnage plan computes a
    # "1,104,323% attainment" — arithmetically exact, semantically garbage
    # (seen on the real anchor pair). A pairing whose total attainment
    # falls outside a generous sane band is a role/unit mismatch: it
    # becomes a declared gap, never a rendered figure.
    actuals = _sheet("JOURNAL PRODUCTION", [
        ["PRODUCTION CPO (T)", 1427800, 1747300, 1331350, 1500000,
         1400000, 1600000],
    ])
    plan = _sheet("PLAN OPERATIONNEL", [
        ["PRODUCTION CPO PREVUE (T)", 136, 136, 136, 136, 136, 136],
    ])
    rep = _analyze(_book(("JOURNAL", actuals), ("BUDGET 2025", plan)))
    mo = rep["model"]["monthly"]
    assert "plan_vs_actual" not in mo
    gaps = rep["model"].get("gaps") or []
    assert any(g["metric"] == "plan_vs_actual"
               and "magnitude" in g["reason"] for g in gaps), gaps


def test_plan_sheet_never_pollutes_the_actuals_spine():
    """Before the partition, the plan's CPO series could pair with the
    actual CPO as volume_secondary — a fabricated 'extraction rate' of
    actual/plan. The plan pool must stay out of the actuals metrics."""
    rep = _analyze(_book(("JOURNAL", _actuals()), ("BUDGET 2025", _plan())))
    mo = rep["model"]["monthly"]
    assert mo["source_sheet"] == "JOURNAL"
    assert mo["metrics"]["volume"]["sheet"] == "JOURNAL"
    assert "volume_secondary" not in mo["metrics"]
    assert "volume_ratio" not in mo["derived"]


def test_misaligned_plan_year_gaps_honestly():
    """A 2026 plan against 2025 actuals shares zero periods: no attainment,
    no guess — one honest gap entry."""
    months_2026 = [dt.date(2026, m, 28) for m in range(7, 13)]
    rep = _analyze(_book(("JOURNAL", _actuals()),
                         ("BUDGET 2026", _plan(months=months_2026))))
    mo = rep["model"]["monthly"]
    assert "plan_vs_actual" not in mo
    gaps = rep["model"]["gaps"]
    assert [(g["metric"], g["grain"]) for g in gaps] == \
        [("plan_vs_actual", "monthly")]
    assert gaps[0]["requires"]


def test_partial_plan_covers_only_shared_months():
    """A plan phased for only the first 4 months: attainment exists there,
    honest None after, totals over the common window only."""
    rep = _analyze(_book(
        ("JOURNAL", _actuals()),
        ("PREVISIONS", _sheet("PLAN", [
            ["PRODUCTION CPO PREVUE (T)", *PLAN_CPO[:4]],
        ], MONTHS[:4]))))
    v = rep["model"]["monthly"]["plan_vs_actual"]["volume"]
    assert v["plan"] == [20, 80, 100, 200, None, None]
    assert v["pct_of_plan"] == [50.0, 50.0, 50.0, 50.0, None, None]
    assert (v["plan_total"], v["actual_total"]) == (400, 200)
    assert v["pct_of_plan_total"] == 50.0


def test_workbook_without_plan_sheet_is_untouched():
    rep = _analyze(_book(("JOURNAL", _actuals())))
    mo = rep["model"]["monthly"]
    assert "plan_vs_actual" not in mo
    assert not rep["model"].get("gaps")


FFB = [50, 200, 250, 500, 400, 80]     # raw input, out-sums CPO -> primary


def _actuals_both(months=MONTHS) -> pd.DataFrame:
    return _sheet("JOURNAL PRODUCTION", [
        ["RECOLTE FFB (T)", *FFB],
        ["PRODUCTION CPO (T)", *CPO],
        ["CHARGES OPEX", *OPEX],
    ], months)


def test_plan_pairs_with_the_matching_volume_series_not_the_role_slot():
    """The journal carries FFB (input, primary volume) AND CPO (output,
    secondary); the plan only plans CPO. Attainment must compare CPO to
    CPO — pairing the FFB actual against a CPO plan fabricated a 139%
    attainment on a quantity nobody planned."""
    rep = _analyze(_book(("JOURNAL", _actuals_both()),
                         ("BUDGET 2025", _plan())))
    pva = rep["model"]["monthly"]["plan_vs_actual"]
    assert "volume" not in pva                     # FFB has no plan
    v = pva["volume_secondary"]
    assert v["plan_label"] == "PRODUCTION CPO PREVUE (T)"
    assert v["pct_of_plan"] == [50.0] * 6
    assert v["pct_of_plan_total"] == 50.0
    assert rep["model"]["monthly"]["metrics"]["volume_secondary"]["label"] \
        == "PRODUCTION CPO (T)"


def test_unmatchable_plan_volume_gaps_instead_of_guessing():
    """A generic 'TONNAGE PREVU' plan against BOTH an FFB and a CPO actual:
    which quantity was planned is unknowable — honest gap, no attainment."""
    plan = _sheet("PLAN", [["TONNAGE PREVU", *PLAN_CPO]])
    rep = _analyze(_book(("JOURNAL", _actuals_both()),
                         ("BUDGET 2025", plan)))
    pva = rep["model"]["monthly"].get("plan_vs_actual") or {}
    assert "volume" not in pva and "volume_secondary" not in pva
    gaps = rep["model"].get("gaps") or []
    assert ("plan_vs_actual", "monthly") in [(g["metric"], g["grain"])
                                             for g in gaps]
