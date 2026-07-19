"""Oracles: file deliverables + signed URLs (SPEC_client_portal_mvp, PR 2/2).

An .xlsx/.pdf turned in for a client is a `kind='file'` deliverable: bytes on
the app's disk, listed in the hub next to dashboards, downloaded through a
short-TTL HMAC-signed URL minted only AFTER the ownership check. The spec's
oracles 6-7 live here: the signed URL expires, and no URL is ever minted for
another client's file.
"""
from __future__ import annotations

import io
import time

import pandas as pd
from fastapi.testclient import TestClient

from app import auth, storage
from app.main import app

client = TestClient(app)

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF")


def _login(client_name: str, email: str) -> TestClient:
    r = client.post(f"/clients/{client_name}/users", json={"email": email})
    assert r.status_code == 200, r.text
    c = TestClient(app)
    assert c.get(r.json()["login_url"],
                 follow_redirects=False).status_code == 303
    return c


def _upload(client_name: str, fname: str, content: bytes,
            group: str = "") -> dict:
    r = client.post(f"/clients/{client_name}/files",
                    files={"file": (fname, content, "application/pdf")},
                    data={"group": group})
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_registers_a_versioned_file_deliverable():
    _upload("PVAK", "modele_tresorerie.pdf", PDF, group="Modèles")
    c = _login("PVAK", "files@pvak.cd")
    items = c.get("/portal/deliverables").json()
    assert len(items) == 1
    d = items[0]
    assert (d["kind"], d["version"], d["group"]) == ("file", 1, "Modèles")
    assert d["status"] is None                 # no checks ran on a file
    # re-uploading the same title bumps the version on the SAME row
    _upload("PVAK", "modele_tresorerie.pdf", PDF + b"v2", group="Modèles")
    items = c.get("/portal/deliverables").json()
    assert len(items) == 1 and items[0]["version"] == 2


def test_detail_mints_signed_url_that_serves_the_exact_bytes():
    _upload("PVAK", "brief_2025.pdf", PDF)
    c = _login("PVAK", "bytes@pvak.cd")
    did = c.get("/portal/deliverables").json()[0]["id"]
    body = c.get(f"/portal/deliverables/{did}").json()
    assert body["kind"] == "file"
    assert "source_ref" not in body
    assert body["expires_at"] > time.time()
    r = client.get(body["signed_url"])         # token is the credential
    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"] == "application/pdf"


def test_signed_url_expires_and_rejects_tampering():
    _upload("PVAK", "expiry.pdf", PDF)
    c = _login("PVAK", "expiry@pvak.cd")
    did = c.get("/portal/deliverables").json()[0]["id"]
    url = c.get(f"/portal/deliverables/{did}").json()["signed_url"]
    # a token minted already-expired is refused
    dead = auth.make_expiring_token("file", did, storage.app_secret(), ttl=-1)
    assert client.get(f"/portal/files/{dead}").status_code == 404
    # bit-flipped signature is refused
    tampered = url[:-4] + ("aaaa" if not url.endswith("aaaa") else "bbbb")
    assert client.get(tampered).status_code == 404
    assert client.get(url).status_code == 200  # the honest one still works


def test_signed_url_requires_ownership():
    _upload("PVAK", "mine.pdf", PDF)
    _upload("SOCOL", "theirs.pdf", PDF)
    pvak = _login("PVAK", "own@pvak.cd")
    socol = _login("SOCOL", "own@socol.cd")
    their_id = socol.get("/portal/deliverables").json()[0]["id"]
    # detail 404s before any URL exists — no URL is ever minted cross-tenant
    assert pvak.get(f"/portal/deliverables/{their_id}").status_code == 404


def test_files_and_dashboards_share_one_hub_list():
    df = pd.DataFrame([
        ["Date", "Description", "Qté", "Cout unit", "Montant"],
        ["2025-01-15", "Semences", 100, 2.4, 240],
        ["2025-02-15", "Machettes", 5, 8, 1290],
        ["2025-03-15", "Pelles", 4, 8, 32],
        ["2025-04-15", "Limes", 10, 1.5, 15],
        ["2025-05-15", "Brouettes", 3, 45, 135],
        ["2025-06-15", "Sacs", 200, 0.25, 50],
    ])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="CAPEX", header=False, index=False,
                engine="openpyxl")
    r = client.post("/analyze", files={
        "file": ("capex.xlsx", buf.getvalue(), "application/octet-stream")})
    aid = r.json()["id"]
    client.post(f"/analyses/{aid}/client", json={"client": "PVAK"})
    fs = client.get(f"/analyses/{aid}/findings").json()
    dec = {f["id"]: "approved" for f in fs["findings"]}
    dec.update({f["id"]: "flagged" for f in fs["unverified"]})
    client.post(f"/analyses/{aid}/decisions", json={"decisions": dec})
    client.post(f"/analyses/{aid}/publish")
    _upload("PVAK", "modele.pdf", PDF)

    c = _login("PVAK", "both@pvak.cd")
    kinds = sorted(i["kind"] for i in c.get("/portal/deliverables").json())
    assert kinds == ["dashboard", "file"]


def test_upload_rejects_the_oversized_and_the_clientless():
    big = b"x" * (25 * 1024 * 1024 + 1)
    r = client.post("/clients/PVAK/files",
                    files={"file": ("big.pdf", big, "application/pdf")})
    assert r.status_code == 413
    r = client.post("/clients/%20/files",
                    files={"file": ("a.pdf", PDF, "application/pdf")})
    assert r.status_code == 422


def test_html_serves_sandboxed_with_scripts_but_no_origin():
    """Interactive HTML dashboards must render their charts (scripts run)
    without ever being able to act on the client's session: the CSP sandbox
    keeps the document in an opaque origin — no cookies, no same-origin API."""
    page = b"<!DOCTYPE html><html><body><canvas></canvas><script>1</script></body></html>"
    r = client.post("/clients/PVAK/files",
                    files={"file": ("tableau_q1.html", page, "text/html")})
    assert r.status_code == 200, r.text
    c = _login("PVAK", "html@pvak.cd")
    did = c.get("/portal/deliverables").json()[0]["id"]
    url = c.get(f"/portal/deliverables/{did}").json()["signed_url"]
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.headers["Content-Security-Policy"] == "sandbox allow-scripts"
    assert resp.headers["Content-Disposition"].startswith("inline")
    # pdf stays CSP-free: no scripts to contain
    _upload("PVAK", "revue_q1.pdf", PDF)
    did2 = [d["id"] for d in c.get("/portal/deliverables").json()
            if d["title"] == "revue_q1"][0]
    url2 = c.get(f"/portal/deliverables/{did2}").json()["signed_url"]
    assert "Content-Security-Policy" not in client.get(url2).headers
