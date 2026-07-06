"""The legacy share mechanism is retired: publish (tests/test_publish.py)
replaced it. These tests pin the retirement — old routes stay dead, and
dead links fail honestly even behind the password wall."""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _analyzed_id() -> str:
    df = pd.DataFrame([["A", "B"], ["x", 1], ["y", 2], ["z", 3]])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="S", header=False, index=False, engine="openpyxl")
    r = client.post("/analyze", files={
        "file": ("share_me.xlsx", buf.getvalue(), "application/octet-stream")})
    return r.json()["id"]


def test_legacy_share_routes_are_retired():
    an_id = _analyzed_id()
    assert client.post(f"/analyses/{an_id}/share").status_code == 404
    assert client.delete(f"/analyses/{an_id}/share").status_code == 404
    assert client.get("/share/token/report").status_code == 404
    assert client.get("/share/token/findings").status_code == 404
    assert client.get("/share/token").status_code == 404


def test_dead_share_links_get_404_not_login_wall(monkeypatch):
    """Someone holding a pre-publish share link should see 'gone', not a
    password prompt for an app they never had credentials to."""
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    assert client.get("/share/old-token/report").status_code == 404
