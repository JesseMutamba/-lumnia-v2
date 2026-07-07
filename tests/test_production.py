"""Process 3: monthly actuals — date-axis matrix series feed the model's
``monthly`` block (volumes, conversion rate per period), the raw material
for the Production view. Same role vocabulary, same derive arithmetic."""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MONTHS = [dt.date(2025, m, 28) for m in range(7, 13)]
FFB = [43.8, 152, 292, 354, 267, 78]
CPO = [7.36, 31.86, 59.76, 75.6, 61.78, 17.72]


def _production_sheet() -> pd.DataFrame:
    return pd.DataFrame([
        ["ENREGISTREMENT PRODUCTION", None, None, None, None, None, None],
        ["SERIE", *MONTHS],
        ["RECOLTE FFB (T)", *FFB],
        ["PRODUCTION CPO (T)", *CPO],
        ["TOTAL", *[f + c for f, c in zip(FFB, CPO)]],
    ])


def _book(*sheets: tuple) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets:
            df.to_excel(xw, sheet_name=name, header=False, index=False)
    return buf.getvalue()


def _analyze(content: bytes, name="prod.xlsx") -> dict:
    return client.post("/analyze", files={
        "file": (name, content, "application/octet-stream")}).json()


def test_monthly_actuals_light_the_model_without_projections():
    rep = _analyze(_book(("PROD", _production_sheet())))
    m = rep["model"]
    assert m is not None
    assert m["scenario_ready"] is False          # no revenue to simulate
    mo = m["monthly"]
    assert len(mo["periods"]) == 6
    assert mo["metrics"]["volume"]["label"] == "RECOLTE FFB (T)"
    assert mo["metrics"]["volume_secondary"]["label"] == "PRODUCTION CPO (T)"
    # TOTAL is a derived row, never a series
    labels = {s["label"] for s in mo["metrics"].values()}
    assert not any("TOTAL" in l for l in labels)
    # extraction rate, month by month
    ratio = mo["derived"]["volume_ratio"]
    assert ratio[0] == round(7.36 / 43.8, 4)
    assert ratio[3] == round(75.6 / 354, 4)


def test_monthly_block_is_none_without_date_matrices():
    csv = (b"Year,Revenue_USD,OPEX_USD\n2025,100,60\n2026,220,90\n"
           b"2027,400,130\n2028,750,180\n")
    rep = client.post("/analyze", files={
        "file": ("fin.csv", csv, "text/csv")}).json()
    assert rep["model"] is not None
    assert rep["model"].get("monthly") is None


def test_subtotal_line_carries_a_role_only_when_alone():
    """'SOUS-TOTAL CAPEX' with unmatched component rows IS the capex
    series; a TOTAL row beside real candidates stays excluded."""
    recap = pd.DataFrame([
        ["RECAP", None, None, None, None],
        [None, 2025, 2026, 2027, 2028],
        ["REVENUES BRUTS", 100, 200, 400, 800],
        ["SOUS-TOTAL CAPEX", 90, 60, 40, 20],          # only capex-ish label
        ["DEVELOPPEMENT NOUVELLES PLANTATIONS", 70, 40, 30, 15],
        ["PRODUCTION FFB", 10, 20, 40, 80],
        ["TOTAL PRODUCTION", 12, 24, 48, 96],          # aggregate: excluded
    ])
    m = _analyze(_book(("RECAP", recap)))["model"]
    assert m["metrics"]["capex"]["label"] == "SOUS-TOTAL CAPEX"
    assert m["metrics"]["volume"]["label"] == "PRODUCTION FFB"


def test_projections_and_actuals_coexist():
    recap = pd.DataFrame([
        ["PROJECTIONS", None, None, None, None],
        [None, 2025, 2026, 2027, 2028],
        ["REVENUS BRUTS", 100, 200, 400, 800],
        ["DEPENSES OPEX", 60, 90, 150, 240],
    ])
    rep = _analyze(_book(("RECAP", recap), ("PROD", _production_sheet())))
    m = rep["model"]
    assert m["metrics"]["revenue"]["values"] == [100, 200, 400, 800]
    assert m["scenario_ready"] is True
    assert m["monthly"]["metrics"]["volume_secondary"]["label"] == \
        "PRODUCTION CPO (T)"
