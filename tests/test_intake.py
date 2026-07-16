"""Oracle: client intake — a client drops their workbook through the hub.

POST /portal/intake requires a client session; the file runs through the
SAME pipeline as an analyst upload and lands in the operator workspace
with the client pre-assigned and NOTHING published. The client gets a
receipt, never the audit. Duplicate bytes don't spawn duplicate analyses,
and an intake can never steal an analysis already assigned to another
client.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _book(salt: str = "") -> bytes:
    rows = [["Métrique", "Jan 2026", "Fév 2026"],
            ["Production CPO (t)", 16, 17.5],
            [f"Charges — production (USD){salt}", 14200, 16900]]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False)
    return buf.getvalue()


def _hub(client_name: str, email: str) -> TestClient:
    r = client.post(f"/clients/{client_name}/users", json={"email": email})
    c = TestClient(app)
    assert c.get(r.json()["login_url"],
                 follow_redirects=False).status_code == 303
    return c


def test_intake_lands_assigned_and_unpublished():
    hub = _hub("Savonnerie Bukavu", "dg@bukavu.cd")
    r = hub.post("/portal/intake", files={
        "file": ("cahier_prod.xlsx", _book("bukavu"),
                 "application/octet-stream")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["received"] is True
    assert "id" not in body            # the client gets a receipt, no handle
    metas = client.get("/analyses").json()
    mine = [a for a in metas if a["filename"] == "cahier_prod.xlsx"
            and a.get("client") == "Savonnerie Bukavu"]
    assert len(mine) == 1
    # audited like any upload, published like nothing
    aid = mine[0]["id"]
    assert client.get(f"/analyses/{aid}/findings").status_code == 200
    assert client.get(f"/analyses/{aid}/publish").json().get("published") in (
        False, None)


def test_intake_requires_a_session():
    fresh = TestClient(app)
    r = fresh.post("/portal/intake", files={
        "file": ("x.xlsx", _book("anon"), "application/octet-stream")})
    assert r.status_code == 401


def test_duplicate_bytes_never_steal_another_clients_analysis():
    book = _book("shared")
    hub_a = _hub("Client Alpha", "a@alpha.cd")
    hub_b = _hub("Client Beta", "b@beta.cd")
    assert hub_a.post("/portal/intake", files={
        "file": ("m.xlsx", book, "application/octet-stream")}).status_code == 200
    assert hub_b.post("/portal/intake", files={
        "file": ("m.xlsx", book, "application/octet-stream")}).status_code == 200
    metas = [a for a in client.get("/analyses").json()
             if a["filename"] == "m.xlsx"]
    assert len(metas) == 1                       # no duplicate row
    assert metas[0]["client"] == "Client Alpha"  # first owner keeps it


def test_unreadable_intake_fails_honest():
    hub = _hub("Client Gamma", "g@gamma.cd")
    r = hub.post("/portal/intake", files={
        "file": ("junk.xlsx", b"not a spreadsheet at all",
                 "application/octet-stream")})
    assert r.status_code == 422
