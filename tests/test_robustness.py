"""Robustness pass: dedupe, size cap, per-sheet crash isolation, .ods."""
from __future__ import annotations

import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main as main_mod
from app.main import app
from tests.fixtures import tidy_sheet, matrix_sheet

client = TestClient(app)


def _xlsx() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        tidy_sheet().to_excel(xw, sheet_name="TIDY", header=False, index=False)
        matrix_sheet().to_excel(xw, sheet_name="MATRIX", header=False, index=False)
    return buf.getvalue()


def test_duplicate_upload_returns_existing_analysis():
    content = _xlsx()
    first = client.post("/analyze", files={"file": ("a.xlsx", content, "application/octet-stream")}).json()
    second = client.post("/analyze", files={"file": ("b-renamed.xlsx", content, "application/octet-stream")}).json()
    assert second["id"] == first["id"]           # same bytes -> same analysis
    assert len(client.get("/analyses").json()) == 1   # library not duplicated


def test_upload_size_cap_413(monkeypatch):
    monkeypatch.setattr(main_mod, "MAX_UPLOAD_BYTES", 1024)
    resp = client.post("/analyze",
                       files={"file": ("big.xlsx", b"x" * 2048, "application/octet-stream")})
    assert resp.status_code == 413
    assert "limit" in resp.json()["detail"]


def test_one_broken_sheet_does_not_sink_the_workbook(monkeypatch):
    from app.pipeline import orient as orient_mod
    real = main_mod.orient_sheet

    def sabotage(df, *a, **k):
        if df.shape == (4, 4):                   # the MATRIX fixture only
            raise RuntimeError("synthetic pipeline crash")
        return real(df, *a, **k)

    monkeypatch.setattr(main_mod, "orient_sheet", sabotage)
    body = client.post("/analyze",
                       files={"file": ("c.xlsx", _xlsx(), "application/octet-stream")}).json()
    by_name = {s["name"]: s for s in body["sheets"]}
    assert by_name["TIDY"]["orientation"] == "tidy"          # survived
    assert by_name["MATRIX"]["orientation"] == "error"       # honest failure
    assert "synthetic pipeline crash" in by_name["MATRIX"]["orientation_reason"]


def test_ods_upload_analyzes():
    pytest.importorskip("python_calamine")
    odf = pytest.importorskip("odf", reason="odfpy needed to write the fixture")  # noqa: F841
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="odf") as xw:
        tidy_sheet().to_excel(xw, sheet_name="TIDY", header=False, index=False)
    resp = client.post("/analyze",
                       files={"file": ("d.ods", buf.getvalue(), "application/octet-stream")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["sheets"][0]["orientation"] == "tidy"
