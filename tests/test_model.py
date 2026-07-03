"""Step 7: the semantic business model (role-tagged series + breakdowns)."""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _year_book() -> bytes:
    """A generic year cross-tab with revenue/opex/capex/volume/area rows, plus
    a line-item cost sheet — NOT the reference workbook."""
    recap = pd.DataFrame([
        ["PROJECTIONS", None, None, None, None],
        [None, 2025, 2026, 2027, 2028],
        ["REVENUS BRUTS", 100, 200, 400, 800],
        ["DEPENSES OPEX", 60, 90, 150, 240],
        ["INVESTISSEMENTS", 500, 300, 100, 50],
        ["PRODUCTION CPO", 10, 20, 40, 80],
        ["HECTARES", 50, 80, 120, 160],
    ])
    items = pd.DataFrame([
        ["Description", "Unité", "Qté", "Montant"],
        ["Semences", "nbre", 10, 500],
        ["Machettes", "pièce", 5, 40],
        ["Pelles", "pièce", 4, 32],
        ["Engrais", "sac", 8, 240],
        ["TOTAL", None, 27, 812],
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        recap.to_excel(xw, sheet_name="RECAP", header=False, index=False)
        items.to_excel(xw, sheet_name="COSTS", header=False, index=False)
    return buf.getvalue()


def _analyze(content: bytes, name="b.xlsx") -> dict:
    return client.post("/analyze", files={
        "file": (name, content, "application/octet-stream")}).json()


def test_model_role_tags_year_series():
    m = _analyze(_year_book())["model"]
    assert m["periods"] == ["2025", "2026", "2027", "2028"]
    assert m["metrics"]["revenue"]["values"] == [100, 200, 400, 800]
    assert m["metrics"]["opex"]["values"] == [60, 90, 150, 240]
    assert m["metrics"]["capex"]["values"] == [500, 300, 100, 50]
    assert m["metrics"]["volume"]["values"] == [10, 20, 40, 80]
    assert m["metrics"]["area"]["values"] == [50, 80, 120, 160]
    assert m["scenario_ready"] is True


def test_model_derives_margin_and_unit_costs():
    m = _analyze(_year_book())["model"]
    assert m["derived"]["margin"] == [40, 110, 250, 560]
    assert m["derived"]["margin_pct"] == [40, 55, 62.5, 70]
    assert m["derived"]["opex_per_volume"] == [6, 4.5, 3.75, 3]


def test_model_breakdown_excludes_totals_row():
    m = _analyze(_year_book())["model"]
    bd = next(b for b in m["breakdowns"] if b["sheet"] == "COSTS")
    labels = [i["label"] for i in bd["items"]]
    assert "Semences" in labels and "TOTAL" not in labels
    assert bd["items"][0] == {"label": "Semences", "value": 500}


def test_no_year_series_means_no_model():
    df = pd.DataFrame([["A", "B"], ["x", 1], ["y", 2], ["z", 3]])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="S", header=False, index=False, engine="openpyxl")
    assert _analyze(buf.getvalue(), "plain.xlsx")["model"] is None
