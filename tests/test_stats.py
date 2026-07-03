"""Usage analytics: the /stats roll-up that feeds the traction memo."""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app import storage
from app.main import app

client = TestClient(app)


def _book(cell: int) -> bytes:
    # cell varies so each upload has distinct bytes (dedup is by sha256)
    df = pd.DataFrame([["A", "B"], ["x", cell], ["y", cell + 1]])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="S", header=False, index=False, engine="openpyxl")
    return buf.getvalue()


def _upload(cell: int, name: str) -> str:
    return client.post("/analyze", files={
        "file": (name, _book(cell), "application/octet-stream")}).json()["id"]


def test_stats_counts_and_grouping():
    before = client.get("/stats").json()["total_analyses"]

    a = _upload(1, "one.xlsx")
    b = _upload(2, "two.xlsx")
    client.post(f"/analyses/{a}/client", json={"client": "PVAK"})
    client.post(f"/analyses/{b}/client", json={"client": "PVAK"})
    _upload(3, "three.xlsx")            # stays unassigned -> "General"

    s = client.get("/stats").json()
    assert s["total_analyses"] == before + 3
    assert s["total_sheets"] >= 3       # one sheet each, at least
    assert s["n_clients"] >= 1
    assert s["days_active"] >= 1
    assert s["first_upload"] and s["last_upload"]

    clients = {c["client"]: c["count"] for c in s["by_client"]}
    assert clients.get("PVAK", 0) >= 2
    assert "General" in clients
    # by_day / by_week are non-empty and sum to the total
    assert sum(d["count"] for d in s["by_day"]) == s["total_analyses"]
    assert sum(w["count"] for w in s["by_week"]) == s["total_analyses"]


def test_week_start_is_monday():
    # 2026-07-03 is a Friday; its ISO week starts Monday 2026-06-29
    assert storage._week_start("2026-07-03") == "2026-06-29"
    assert storage._week_start("not-a-date") is None
