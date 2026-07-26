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


def test_trailing_blank_bloat_is_trimmed_not_refused(monkeypatch):
    # Real workbooks carry huge formatted-but-empty used ranges (a 65k-row
    # tail after 96 real rows). Trailing all-blank rows/columns are trimmed
    # at ingest — every kept cell keeps its exact (row, col), so A1 refs
    # stay true — and the file passes the cell cap instead of a 422.
    monkeypatch.setattr(main_mod, "MAX_TOTAL_CELLS", 50_000)
    months = ["janv. 2025", "févr. 2025", "mars 2025",
              "avr. 2025", "mai 2025", "juin 2025"]
    rows = [["JOURNAL PRODUCTION", *([None] * 6)],
            ["SERIE", *months],
            ["PRODUCTION CPO (T)", 10, 12, 11, 14, 13, 12],
            ["CHARGES OPEX", 5, 5, 6, 5, 6, 5]]
    df = pd.DataFrame(rows + [[None] * 7] * 60_000)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="JOURNAL", header=False, index=False)
    r = client.post("/analyze", files={
        "file": ("bloat.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200, r.text[:300]
    sheet = r.json()["sheets"][0]
    assert sheet["n_rows"] == 4                    # tail gone, content intact
    ser = {s["label"]: s for s in
           sheet["tidy"]["summary"]["chart"]["series_all"]}
    # positional identity survives the trim: values still cite row 3
    assert ser["PRODUCTION CPO (T)"]["cells"][0] == ["B3"]
    assert ser["PRODUCTION CPO (T)"]["cells"][5] == ["G3"]


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


def test_bom_and_cr_only_csv():
    """UTF-8 BOM + CR-only line endings (old Mac exports): the BOM must not
    leak into the first header cell, and every row must be read."""
    from app.pipeline.ingest import read_upload
    content = "﻿PLAYER,PTS\rWestbrook,31.6\rHarden,29.1\r".encode("utf-8")
    sheets = read_upload(content, "nba.csv")
    df = list(sheets.values())[0]
    assert df.iloc[0, 0] == "PLAYER"          # no ﻿ prefix
    assert len(df) == 3                        # header + 2 data rows
