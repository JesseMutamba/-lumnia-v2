"""Oracle: passwordless client sign-in — email in, link out, zero leaks.

POST /portal/request-link is PUBLIC. A registered contact gets a fresh
sign-in link by email; an unknown email becomes a pending access request
on the operator's desk. Both answers are byte-identical (no enumeration),
a throttle caps mails per address, and approval is the only path from
unknown email to workbooks — it creates the contact under a client the
OPERATOR chose and emails the link.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import mailer
from app.main import app

client = TestClient(app)


def _known(name: str, email: str) -> None:
    r = client.post(f"/clients/{name}/users", json={"email": email})
    assert r.status_code == 200, r.text


def _smtp_on(monkeypatch, sent: list) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.cd")
    monkeypatch.setenv("SMTP_FROM", "bonjour@lumnia.cd")
    monkeypatch.setattr(mailer, "_deliver", lambda msg, cfg: sent.append(msg))


def test_known_and_unknown_answers_are_identical(monkeypatch):
    sent: list = []
    _smtp_on(monkeypatch, sent)
    _known("Minoterie Goma", "dg@goma.cd")
    r_known = client.post("/portal/request-link", data={"email": "dg@goma.cd"})
    r_unknown = client.post("/portal/request-link",
                            data={"email": "inconnu@nulpart.cd"})
    assert r_known.status_code == r_unknown.status_code == 200
    assert r_known.text == r_unknown.text          # byte-identical, no leak
    # but behind the curtain: one mail, one pending request
    assert len(sent) == 1 and sent[0]["To"] == "dg@goma.cd"
    pending = client.get("/access-requests").json()
    assert [p["email"] for p in pending
            if p["email"] == "inconnu@nulpart.cd"]


def test_mailed_link_signs_in(monkeypatch):
    sent: list = []
    _smtp_on(monkeypatch, sent)
    _known("Scierie Beni", "chef@beni.cd")
    client.post("/portal/request-link", data={"email": "chef@beni.cd"})
    body = sent[-1].get_content()
    link = next(w for w in body.split() if "/portal/login/" in w)
    path = link[link.index("/portal/login/"):]
    fresh = TestClient(app)
    assert fresh.get(path, follow_redirects=False).status_code == 303
    assert fresh.get("/portal/me").json()["email"] == "chef@beni.cd"


def test_throttle_caps_mails_per_hour(monkeypatch):
    sent: list = []
    _smtp_on(monkeypatch, sent)
    _known("Brasserie Bunia", "compta@bunia.cd")
    for _ in range(5):
        r = client.post("/portal/request-link",
                        data={"email": "compta@bunia.cd"})
        assert r.status_code == 200                # neutral every time
    assert len(sent) == 3                          # but only 3 mails go out


def test_approve_creates_contact_and_mails(monkeypatch):
    sent: list = []
    _smtp_on(monkeypatch, sent)
    client.post("/portal/request-link", data={"email": "neuf@kasindi.cd"})
    req = next(p for p in client.get("/access-requests").json()
               if p["email"] == "neuf@kasindi.cd")
    r = client.post(f"/access-requests/{req['id']}/approve",
                    json={"client": "Dépôt Kasindi"})
    assert r.status_code == 200, r.text
    assert r.json()["emailed"] is True
    assert sent[-1]["To"] == "neuf@kasindi.cd"
    users = client.get("/clients/Dépôt Kasindi/users").json()
    assert [u for u in users if u["email"] == "neuf@kasindi.cd"]
    # the queue entry is gone
    assert not [p for p in client.get("/access-requests").json()
                if p["email"] == "neuf@kasindi.cd"]


def test_dismiss_removes_without_contact(monkeypatch):
    sent: list = []
    _smtp_on(monkeypatch, sent)
    client.post("/portal/request-link", data={"email": "spam@ailleurs.com"})
    req = next(p for p in client.get("/access-requests").json()
               if p["email"] == "spam@ailleurs.com")
    assert client.delete(
        f"/access-requests/{req['id']}").json()["dismissed"] is True
    assert not [p for p in client.get("/access-requests").json()
                if p["email"] == "spam@ailleurs.com"]
    assert not sent


def test_signin_page_is_public_and_bilingual():
    r = client.get("/portal/signin")
    assert r.status_code == 200
    assert "email" in r.text.lower()
    assert "lien de connexion" in r.text          # FR present, always
