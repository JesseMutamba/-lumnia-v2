"""Oracle: per-client report portals.

A portal is one unguessable link per client that lists exactly that client's
*currently published* dashboards. Two guarantees, both load-bearing:

* **No leak.** The portal joins the frozen exec-only snapshots — never the
  live report — so analyst-only material cannot travel through it, and it
  never lists an unpublished analysis or another client's work.
* **Live.** Publishing another of the client's workbooks grows the list;
  revoking a publish (or the portal) shrinks it / kills the link at once.
"""
from __future__ import annotations

import io
import json

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# nothing analyst-only may ride along inside a portal payload
ANALYST_ONLY_KEYS = {"sheets", "findings", "mismatches", "missingness",
                     "preview", "records", "eda", "content", "decisions"}


def _book() -> bytes:
    """A broken qty x unit = total row (delta 1250) plus a dated column, so
    every analysis has real findings to decide and a story chart to freeze."""
    df = pd.DataFrame([
        ["Date", "Description", "Qté", "Cout unit", "Montant"],
        ["2025-01-15", "Semences", 100, 2.4, 240],
        ["2025-02-15", "Machettes", 5, 8, 1290],   # should be 40
        ["2025-03-15", "Pelles", 4, 8, 32],
    ])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="CAPEX", header=False, index=False,
                engine="openpyxl")
    return buf.getvalue()


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _analysis(salt: bytes, client_name: str | None = None) -> str:
    r = client.post("/analyze", files={
        "file": ("capex.xlsx", _book() + salt, "application/octet-stream")})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    if client_name:
        assert client.post(f"/analyses/{aid}/client",
                           json={"client": client_name}).status_code == 200
    return aid


def _publish(aid: str) -> str:
    """Decide every finding, then publish; returns the public token."""
    fs = client.get(f"/analyses/{aid}/findings").json()
    decisions = {f["id"]: "approved" for f in fs["findings"]}
    decisions.update({f["id"]: "flagged" for f in fs["unverified"]})
    if decisions:
        assert client.post(f"/analyses/{aid}/decisions",
                           json={"decisions": decisions}).status_code == 200
    r = client.post(f"/analyses/{aid}/publish")
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _portal_token(client_name: str) -> str:
    r = client.post(f"/clients/{client_name}/portal")
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_portal_lists_only_this_clients_published_dashboards():
    """The core: exactly this client's published work — not the unpublished
    one, not another client's."""
    pvak_pub = _analysis(b"pvak-1", "PVAK")
    _analysis(b"pvak-2", "PVAK")            # PVAK, deliberately left UNpublished
    socol_pub = _analysis(b"socol-1", "SOCOL")
    pub_token = _publish(pvak_pub)
    _publish(socol_pub)

    token = _portal_token("PVAK")
    body = client.get(f"/portal/{token}").json()

    assert body["client"] == "PVAK"
    assert body["count"] == 1
    # exactly the published PVAK dashboard, keyed by its own public token —
    # not the unpublished PVAK workbook, not the SOCOL one
    assert [d["token"] for d in body["dashboards"]] == [pub_token]


def test_portal_payload_carries_no_analyst_material():
    aid = _analysis(b"leak", "PVAK")
    _publish(aid)
    token = _portal_token("PVAK")
    body = client.get(f"/portal/{token}").json()
    keys = set(_walk_keys(body))
    assert not keys & ANALYST_ONLY_KEYS, keys & ANALYST_ONLY_KEYS
    # the broken row's label lives only in analyst detail — never here
    assert "Machettes" not in json.dumps(body, ensure_ascii=False)


def test_portal_is_live_grows_on_publish_shrinks_on_revoke():
    a1 = _analysis(b"live-1", "PVAK")
    a2 = _analysis(b"live-2", "PVAK")
    _publish(a1)
    token = _portal_token("PVAK")
    assert client.get(f"/portal/{token}").json()["count"] == 1

    _publish(a2)                                        # grows, same portal link
    assert client.get(f"/portal/{token}").json()["count"] == 2

    client.delete(f"/analyses/{a1}/publish")            # revoke one publish
    assert client.get(f"/portal/{token}").json()["count"] == 1


def test_portal_token_is_stable_and_unguessable():
    _publish(_analysis(b"stable", "PVAK"))
    one = client.post("/clients/PVAK/portal").json()["token"]
    two = client.post("/clients/PVAK/portal").json()["token"]
    assert one == two                      # re-enabling returns the same link
    assert len(one) >= 16                   # not a guessable slug


def test_unknown_and_revoked_portal_tokens_404():
    _publish(_analysis(b"revoke", "PVAK"))
    token = _portal_token("PVAK")
    assert client.get("/portal/not-a-real-token").status_code == 404
    assert client.delete("/clients/PVAK/portal").json()["revoked"] is True
    assert client.get(f"/portal/{token}").status_code == 404
    assert client.get(f"/portal/{token}/page").status_code == 404


def test_portal_page_bypasses_password(monkeypatch):
    aid = _analysis(b"gate", "PVAK")
    _publish(aid)
    token = _portal_token("PVAK")
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    assert client.get("/analyses").status_code == 401        # app stays gated
    assert client.get(f"/portal/{token}").status_code == 200  # portal is public
    assert client.get(f"/portal/{token}/page").status_code == 200
