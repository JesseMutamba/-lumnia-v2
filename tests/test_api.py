import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures import tidy_sheet, matrix_sheet, form_sheet, unknown_sheet

client = TestClient(app)


def _xlsx_bytes() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        tidy_sheet().to_excel(xw, sheet_name="TIDY", header=False, index=False)
        matrix_sheet().to_excel(xw, sheet_name="MATRIX", header=False, index=False)
        form_sheet().to_excel(xw, sheet_name="FORM", header=False, index=False)
        unknown_sheet().to_excel(xw, sheet_name="RAW", header=False, index=False)
    return buf.getvalue()


def test_index_serves_ui():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Lumnia" in resp.text


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_analyze_multi_sheet_workbook():
    files = {"file": ("book.xlsx", _xlsx_bytes(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    resp = client.post("/analyze", files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_sheets"] == 4

    by_name = {s["name"]: s for s in body["sheets"]}
    assert by_name["TIDY"]["orientation"] == "tidy"
    assert by_name["MATRIX"]["orientation"] == "matrix"
    assert by_name["FORM"]["orientation"] == "form"
    assert by_name["RAW"]["orientation"] == "unknown"

    # Matrix sheet is unpivoted to long form via the API.
    assert by_name["MATRIX"]["tidy"]["columns"] == ["METRIC", "period", "value"]
    assert by_name["RAW"]["tidy"] is None


def test_analyze_rejects_empty_upload():
    files = {"file": ("empty.csv", b"", "text/csv")}
    resp = client.post("/analyze", files=files)
    assert resp.status_code == 400


def test_analyze_csv_single_sheet():
    csv = b"PARCELLE,ANNEE,HA\nBLOC 2019,2019,10\nBLOC 2020,2020,8\n"
    files = {"file": ("data.csv", csv, "text/csv")}
    resp = client.post("/analyze", files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_sheets"] == 1
    assert body["sheets"][0]["orientation"] == "tidy"
