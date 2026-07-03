"""Read-only share links: the unguessable token is the credential."""
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


def test_share_link_is_idempotent_and_serves_report():
    an_id = _analyzed_id()
    t1 = client.post(f"/analyses/{an_id}/share").json()
    t2 = client.post(f"/analyses/{an_id}/share").json()
    assert t1["token"] == t2["token"]
    assert t1["url"] == f"/share/{t1['token']}"

    page = client.get(t1["url"])
    assert page.status_code == 200 and "LUMNIA" in page.text.upper()
    rep = client.get(f"{t1['url']}/report").json()
    assert rep["filename"] == "share_me.xlsx"
    fnd = client.get(f"{t1['url']}/findings").json()
    assert fnd["id"] == "shared"


def test_share_bypasses_password_but_only_readonly_routes(monkeypatch):
    an_id = _analyzed_id()
    token = client.post(f"/analyses/{an_id}/share").json()["token"]
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    # the app itself stays gated
    assert client.get("/analyses").status_code == 401
    assert client.post(f"/analyses/{an_id}/share").status_code == 401
    # ...but the shared view works without a session
    assert client.get(f"/share/{token}").status_code == 200
    assert client.get(f"/share/{token}/report").status_code == 200
    assert client.get(f"/share/{token}/findings").status_code == 200


def test_unknown_token_404s():
    assert client.get("/share/not-a-real-token").status_code == 404
    assert client.get("/share/not-a-real-token/report").status_code == 404


def test_revoke_and_delete_kill_the_link():
    an_id = _analyzed_id()
    token = client.post(f"/analyses/{an_id}/share").json()["token"]
    assert client.delete(f"/analyses/{an_id}/share").json()["revoked"] is True
    assert client.get(f"/share/{token}/report").status_code == 404
    # re-share, then deleting the analysis revokes too
    token = client.post(f"/analyses/{an_id}/share").json()["token"]
    client.delete(f"/analyses/{an_id}")
    assert client.get(f"/share/{token}/report").status_code == 404
