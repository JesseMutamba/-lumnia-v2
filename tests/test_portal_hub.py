"""Oracles: the authed client hub (SPEC_client_portal_mvp, PR 1 of 2).

A client_user holds an analyst-issued signed login link; exchanging it sets a
client session whose ``client_id`` is ALWAYS derived server-side. The hub
lists that client's deliverables — dashboards recorded automatically by the
publish path — and serves each one's frozen exec-only snapshot. The boundary
that must not fail: one login only ever sees its own client's rows, and a
cross-tenant id answers 404, never 200, never 403.
"""
from __future__ import annotations

import io
import json

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ANALYST_ONLY_KEYS = {"sheets", "findings", "mismatches", "missingness",
                     "preview", "records", "eda", "content", "decisions"}


def _book() -> bytes:
    df = pd.DataFrame([
        ["Date", "Description", "Qté", "Cout unit", "Montant"],
        ["2025-01-15", "Semences", 100, 2.4, 240],
        ["2025-02-15", "Machettes", 5, 8, 1290],   # planted delta 1250
        ["2025-03-15", "Pelles", 4, 8, 32],
        ["2025-04-15", "Limes", 10, 1.5, 15],
        ["2025-05-15", "Brouettes", 3, 45, 135],
        ["2025-06-15", "Sacs", 200, 0.25, 50],
    ])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="CAPEX", header=False, index=False,
                engine="openpyxl")
    return buf.getvalue()


def _published_analysis(salt: bytes, client_name: str) -> str:
    r = client.post("/analyze", files={
        "file": ("capex.xlsx", _book() + salt, "application/octet-stream")})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert client.post(f"/analyses/{aid}/client",
                       json={"client": client_name}).status_code == 200
    fs = client.get(f"/analyses/{aid}/findings").json()
    dec = {f["id"]: "approved" for f in fs["findings"]}
    dec.update({f["id"]: "flagged" for f in fs["unverified"]})
    if dec:
        client.post(f"/analyses/{aid}/decisions", json={"decisions": dec})
    assert client.post(f"/analyses/{aid}/publish").status_code == 200
    return aid


def _login(client_name: str, email: str) -> TestClient:
    """Mint a login link for a fresh client_user and exchange it: returns an
    authenticated TestClient holding the client-session cookie."""
    r = client.post(f"/clients/{client_name}/users", json={"email": email})
    assert r.status_code == 200, r.text
    url = r.json()["login_url"]
    c = TestClient(app)
    r = c.get(url, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


# --- oracle 1: the 403 gate ------------------------------------------------

def test_unknown_or_revoked_login_link_is_refused():
    assert client.get("/portal/login/not-a-real-token",
                      follow_redirects=False).status_code == 403
    _published_analysis(b"gate", "PVAK")
    r = client.post("/clients/PVAK/users", json={"email": "gate@pvak.cd"})
    uid = r.json()["id"]
    url = r.json()["login_url"]
    assert client.delete(f"/clients/PVAK/users/{uid}").json()["revoked"] is True
    assert client.get(url, follow_redirects=False).status_code == 403


def test_me_requires_a_session():
    assert client.get("/portal/me").status_code == 401
    assert client.get("/portal/deliverables").status_code == 401


def test_revoking_a_user_kills_the_live_session():
    _published_analysis(b"kill", "PVAK")
    c = _login("PVAK", "kill@pvak.cd")
    assert c.get("/portal/me").status_code == 200
    uid = [u for u in client.get("/clients/PVAK/users").json()
           if u["email"] == "kill@pvak.cd"][0]["id"]
    client.delete(f"/clients/PVAK/users/{uid}")
    assert c.get("/portal/me").status_code == 403


# --- oracles 2-4: tenant isolation ------------------------------------------

def test_list_scoped_to_client():
    _published_analysis(b"pvak-a", "PVAK")
    _published_analysis(b"pvak-b", "PVAK")
    _published_analysis(b"socol-a", "SOCOL")
    c = _login("PVAK", "list@pvak.cd")
    me = c.get("/portal/me").json()
    assert me["client"]["name"] == "PVAK" and me["email"] == "list@pvak.cd"
    items = c.get("/portal/deliverables").json()
    assert len(items) == 2                    # exactly PVAK's, never SOCOL's
    assert all(i["kind"] == "dashboard" for i in items)


def test_cross_tenant_get_404():
    _published_analysis(b"mine", "PVAK")
    _published_analysis(b"theirs", "SOCOL")
    pvak = _login("PVAK", "cross@pvak.cd")
    socol = _login("SOCOL", "cross@socol.cd")
    their_id = socol.get("/portal/deliverables").json()[0]["id"]
    assert pvak.get(f"/portal/deliverables/{their_id}").status_code == 404
    assert pvak.get("/portal/deliverables/nonexistent").status_code == 404


def test_list_omits_source_ref():
    _published_analysis(b"refs", "PVAK")
    c = _login("PVAK", "refs@pvak.cd")
    items = c.get("/portal/deliverables").json()
    keys = set(_walk_keys(items))
    assert "source_ref" not in keys
    assert not keys & ANALYST_ONLY_KEYS


# --- oracle 5: curated payload only ------------------------------------------

def test_dashboard_payload_is_curated():
    _published_analysis(b"leak", "PVAK")
    c = _login("PVAK", "leak@pvak.cd")
    did = c.get("/portal/deliverables").json()[0]["id"]
    body = c.get(f"/portal/deliverables/{did}").json()
    assert body["kind"] == "dashboard"
    keys = set(_walk_keys(body))
    assert not keys & ANALYST_ONLY_KEYS, keys & ANALYST_ONLY_KEYS
    assert "Machettes" not in json.dumps(body, ensure_ascii=False)


# --- oracle 8: publish upserts, revoke removes -------------------------------

def test_publish_upserts_exactly_one_deliverable_and_versions():
    aid = _published_analysis(b"upsert", "PVAK")
    c = _login("PVAK", "upsert@pvak.cd")
    items = c.get("/portal/deliverables").json()
    assert len(items) == 1
    assert items[0]["version"] == 1
    assert items[0]["status"] in ("reconciled", "flagged")
    client.post(f"/analyses/{aid}/publish")              # republish: v2
    items = c.get("/portal/deliverables").json()
    assert len(items) == 1                               # same row, not two
    assert items[0]["version"] == 2


def test_revoke_publish_removes_the_deliverable():
    aid = _published_analysis(b"revoke", "PVAK")
    c = _login("PVAK", "revoke@pvak.cd")
    assert len(c.get("/portal/deliverables").json()) == 1
    client.delete(f"/analyses/{aid}/publish")
    assert c.get("/portal/deliverables").json() == []


def test_unpublished_or_unassigned_analyses_never_appear():
    r = client.post("/analyze", files={
        "file": ("capex.xlsx", _book() + b"noclient", "application/octet-stream")})
    aid = r.json()["id"]                     # no client label, not published
    _published_analysis(b"visible", "PVAK")
    c = _login("PVAK", "only@pvak.cd")
    items = c.get("/portal/deliverables").json()
    assert len(items) == 1


# --- coexistence: the token portal survives ---------------------------------

def test_hub_routes_coexist_with_token_portal():
    _published_analysis(b"coexist", "PVAK")
    tok = client.post("/clients/PVAK/portal").json()["token"]
    assert client.get(f"/portal/{tok}").json()["count"] == 1
    c = _login("PVAK", "coexist@pvak.cd")
    assert c.get("/portal/me").status_code == 200        # not eaten by {token}
    assert c.get("/portal/hub").status_code == 200


def test_hub_page_bypasses_analyst_password(monkeypatch):
    _published_analysis(b"pw", "PVAK")
    c = _login("PVAK", "pw@pvak.cd")
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    assert client.get("/analyses").status_code == 401     # analyst side gated
    assert c.get("/portal/me").status_code == 200         # client side works
    assert c.get("/portal/deliverables").status_code == 200


def test_logout_kills_the_client_session():
    """Shared phones are the normal case: sign out must end the session on
    this device, immediately."""
    _published_analysis(b"bye", "PVAK")
    c = _login("PVAK", "adieu@pvak.cd")
    assert c.get("/portal/me").status_code == 200
    r = c.get("/portal/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/portal/signin"
    assert c.get("/portal/me").status_code == 401


def test_dashboard_deliverable_title_is_humanized():
    """'PVAK_T1_2026.xlsx' is a filename; the client reads 'PVAK T1 2026'."""
    r = client.post("/analyze", files={
        "file": ("PVAK_T1_2026.xlsx", _book() + b"humanize",
                 "application/octet-stream")})
    aid = r.json()["id"]
    assert client.post(f"/analyses/{aid}/client",
                       json={"client": "PVAK"}).status_code == 200
    fs = client.get(f"/analyses/{aid}/findings").json()
    dec = {f["id"]: "approved" for f in fs["findings"]}
    dec.update({f["id"]: "flagged" for f in fs["unverified"]})
    if dec:
        client.post(f"/analyses/{aid}/decisions", json={"decisions": dec})
    assert client.post(f"/analyses/{aid}/publish").status_code == 200
    c = _login("PVAK", "titre@pvak.cd")
    titles = [d["title"] for d in c.get("/portal/deliverables").json()]
    assert "PVAK T1 2026" in titles


def test_analyst_gate_is_closed_by_default_and_opens_per_client():
    """The executive view is the product: a client's dashboard deliverable
    carries NO analyst material unless the operator opens the gate for
    that client — then the live audit detail rides along, read-only."""
    _published_analysis(b"gate-a", "PVAK")
    c = _login("PVAK", "auditeur@pvak.cd")
    did = c.get("/portal/deliverables").json()[0]["id"]
    d = c.get(f"/portal/deliverables/{did}").json()
    assert "audit_detail" not in d                 # closed by default
    assert "_source_ref" not in d and "source_ref" not in d

    assert client.post("/clients/PVAK/analyst-access",
                       json={"enabled": True}).status_code == 200
    d2 = c.get(f"/portal/deliverables/{did}").json()
    assert "audit_detail" in d2
    ad = d2["audit_detail"]
    assert {"n_verified_relations", "n_mismatched_relations",
            "findings"} <= set(ad)
    assert "_source_ref" not in d2 and "source_ref" not in d2

    # the gate closes again, and other clients were never affected
    client.post("/clients/PVAK/analyst-access", json={"enabled": False})
    assert "audit_detail" not in c.get(f"/portal/deliverables/{did}").json()
    assert client.post("/clients/Inconnu/analyst-access",
                       json={"enabled": True}).status_code == 404
