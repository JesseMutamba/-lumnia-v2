"""Oracle: the client access panel — the operator-side onboarding surface.

GET /clients lists every client with contact + deliverable counts, so the
workbench can render access management without N+1 calls. POST
/clients/{c}/users/{uid}/link re-mints a signed login link for an EXISTING
contact (creation is the only other moment a link exists). Revocation kills
the identity: even a freshly minted link must die with it.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _book() -> bytes:
    rows = [["Métrique", "Jan 2026", "Fév 2026"],
            ["Production CPO (t)", 16, 17.5],
            ["Charges — production (USD)", 14200, 16900]]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False)
    return buf.getvalue()


def _client_with_analysis(name: str, salt: bytes) -> str:
    r = client.post("/analyze", files={
        "file": ("cahier.xlsx", _book() + salt, "application/octet-stream")})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert client.post(f"/analyses/{aid}/client",
                       json={"client": name}).status_code == 200
    return aid


def test_clients_list_carries_counts():
    _client_with_analysis("Huilerie Mbandaka", b"counts")
    r = client.post("/clients/Huilerie Mbandaka/users",
                    json={"email": "dg@mbandaka.cd"})
    assert r.status_code == 200
    rows = client.get("/clients").json()
    mine = [c for c in rows if c["name"] == "Huilerie Mbandaka"]
    assert len(mine) == 1
    assert mine[0]["n_users"] == 1
    assert mine[0]["slug"]
    # counts are ints, present even when zero elsewhere
    assert isinstance(mine[0]["n_deliverables"], int)


def test_reminted_link_signs_in_existing_contact():
    _client_with_analysis("Plantation Boma", b"remint")
    uid = client.post("/clients/Plantation Boma/users",
                      json={"email": "compta@boma.cd"}).json()["id"]
    r = client.post(f"/clients/Plantation Boma/users/{uid}/link")
    assert r.status_code == 200
    url = r.json()["login_url"]
    assert url.startswith("/portal/login/")
    fresh = TestClient(app)
    assert fresh.get(url, follow_redirects=False).status_code == 303
    assert fresh.get("/portal/me").json()["email"] == "compta@boma.cd"


def test_link_for_foreign_or_unknown_contact_404s():
    _client_with_analysis("Ferme Kasaï", b"foreign")
    _client_with_analysis("Ferme Uélé", b"foreign2")
    uid = client.post("/clients/Ferme Kasaï/users",
                      json={"email": "chef@kasai.cd"}).json()["id"]
    # right user, wrong client — refused, no cross-client link minting
    assert client.post(
        f"/clients/Ferme Uélé/users/{uid}/link").status_code == 404
    assert client.post(
        "/clients/Ferme Kasaï/users/000000000000/link").status_code == 404


def test_revocation_kills_even_a_fresh_link():
    _client_with_analysis("Comptoir Kivu", b"revoke")
    uid = client.post("/clients/Comptoir Kivu/users",
                      json={"email": "achats@kivu.cd"}).json()["id"]
    url = client.post(f"/clients/Comptoir Kivu/users/{uid}/link"
                      ).json()["login_url"]
    assert client.delete(
        f"/clients/Comptoir Kivu/users/{uid}").json()["revoked"] is True
    fresh = TestClient(app)
    assert fresh.get(url, follow_redirects=False).status_code == 403
