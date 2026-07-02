"""Step 5: persistence + the stored-analyses API surface."""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app import storage
from tests.fixtures import tidy_sheet, matrix_sheet

client = TestClient(app)


def _upload() -> dict:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        tidy_sheet().to_excel(xw, sheet_name="TIDY", header=False, index=False)
        matrix_sheet().to_excel(xw, sheet_name="MATRIX", header=False, index=False)
    files = {"file": ("book.xlsx", buf.getvalue(), "application/octet-stream")}
    resp = client.post("/analyze", files=files)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# storage module
# --------------------------------------------------------------------------- #
def test_save_and_get_roundtrip():
    aid = storage.save_analysis("f.xlsx", b"bytes", {"n_sheets": 2, "x": "é"})
    # The generated id is stamped into the stored report.
    assert storage.get_report(aid) == {"n_sheets": 2, "x": "é", "id": aid}
    assert storage.get_content(aid) == ("f.xlsx", b"bytes")


def test_get_missing_returns_none():
    assert storage.get_report("nope") is None
    assert storage.get_content("nope") is None
    assert storage.delete_analysis("nope") is False
    assert storage.update_report("nope", {}) is False


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #
def test_analyze_persists_and_returns_id():
    body = _upload()
    assert body["id"]
    stored = client.get(f"/analyses/{body['id']}")
    assert stored.status_code == 200
    assert stored.json() == body            # stored report == live response


def test_list_analyses_newest_first_metadata_only():
    a = _upload()
    b = _upload()
    resp = client.get("/analyses")
    assert resp.status_code == 200
    items = resp.json()
    assert {i["id"] for i in items} >= {a["id"], b["id"]}
    meta = items[0]
    assert set(meta) == {"id", "filename", "uploaded_at", "reran_at",
                         "size_bytes", "n_sheets"}
    assert meta["n_sheets"] == 2
    assert meta["size_bytes"] > 0


def test_rerun_reanalyzes_stored_bytes():
    body = _upload()
    resp = client.post(f"/analyses/{body['id']}/rerun")
    assert resp.status_code == 200
    rerun = resp.json()
    assert rerun["id"] == body["id"]
    assert rerun["n_sheets"] == 2
    # rerun timestamp is recorded in the listing
    meta = next(i for i in client.get("/analyses").json()
                if i["id"] == body["id"])
    assert meta["reran_at"] is not None


def test_delete_analysis():
    body = _upload()
    resp = client.delete(f"/analyses/{body['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"id": body["id"], "deleted": True}
    assert client.get(f"/analyses/{body['id']}").status_code == 404
    assert client.delete(f"/analyses/{body['id']}").status_code == 404


def test_missing_analysis_is_404():
    assert client.get("/analyses/nothere").status_code == 404
    assert client.post("/analyses/nothere/rerun").status_code == 404
