"""Workbook-level findings endpoint: aggregation across sheets and panels."""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _capex_sheet(broken_value: float) -> pd.DataFrame:
    """qty x unit = total table with ONE broken row worth |broken_value - 40|."""
    return pd.DataFrame([
        ["Description", "Qté", "Cout unit", "Montant"],
        ["Semences", 100, 2.4, 240],
        ["Machettes", 5, 8, broken_value],   # should be 40
        ["Pelles", 4, 8, 32],
        ["Limes", 10, 1.5, 15],
        ["Brouettes", 3, 45, 135],
        ["Sacs", 200, 0.25, 50],
    ])


def _clean_and_broken_panels() -> pd.DataFrame:
    """Two side-by-side tables; only the RIGHT panel has a broken total row."""
    left = [
        ["PARCELLE", "HA", "REGIMES"],
        ["BLOC 1", 10, 100],
        ["BLOC 2", 12, 120],
        ["BLOC 3", 8, 80],
        [None, None, None],
    ]
    right = [
        ["Poste", "Total", "Jan", "Fev", "Mar"],
        ["Salaires", 60, 10, 20, 30],
        ["Carburant", 15, 5, 5, 5],
        ["Entretien", 99, 3, 3, 3],          # should be 9 -> delta 90
        ["Divers", 6, 2, 2, 2],
    ]
    return pd.DataFrame([l + [None] + r for l, r in zip(left, right)])


def _upload() -> dict:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        # big mismatch: 4000 vs 40 -> delta 3960
        _capex_sheet(4000).to_excel(xw, sheet_name="CAPEX", header=False, index=False)
        # small mismatch inside a panel: delta 90
        _clean_and_broken_panels().to_excel(xw, sheet_name="SIDE", header=False, index=False)
    files = {"file": ("book.xlsx", buf.getvalue(), "application/octet-stream")}
    resp = client.post("/analyze", files=files)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_findings_aggregate_across_sheets_and_panels():
    body = _upload()
    resp = client.get(f"/analyses/{body['id']}/findings")
    assert resp.status_code == 200
    audit = resp.json()

    assert audit["id"] == body["id"]
    assert audit["n_mismatched_relations"] >= 2
    sheets_with_findings = {f["sheet"] for f in audit["findings"]}
    assert sheets_with_findings == {"CAPEX", "SIDE"}

    # Ranked by money impact: the 3960 capex hole outranks the 90 panel one.
    assert audit["findings"][0]["sheet"] == "CAPEX"
    assert audit["findings"][0]["total_abs_delta"] == 3960
    assert audit["findings"][0]["panel"] is None

    side = next(f for f in audit["findings"] if f["sheet"] == "SIDE")
    assert side["panel"] is not None            # found inside a panel
    assert side["total_abs_delta"] == 90
    assert side["mismatches"][0]["label"] == "Entretien"

    # Grand total = sum of mismatch impacts.
    assert audit["total_abs_delta"] >= 4050


def test_findings_include_verified_relations():
    body = _upload()
    audit = client.get(f"/analyses/{body['id']}/findings").json()
    # The discovered relations themselves are reported, mismatched or not;
    # nothing verified is hidden.
    assert audit["n_verified_relations"] + audit["n_mismatched_relations"] > 0
    for entry in audit["verified"]:
        assert entry["status"] == "ok"
        assert entry["n_mismatched"] == 0


def test_findings_missing_analysis_is_404():
    assert client.get("/analyses/nothere/findings").status_code == 404
