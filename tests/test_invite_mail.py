"""Oracle: emailed invites — stdlib SMTP, env-configured, honest walls.

POST /clients/{c}/users/{uid}/invite mints a fresh sign-in link and mails
it. No SMTP_HOST -> 503 (the copy-link path always works instead). The
message is built by app.mailer and handed to its _deliver seam — tests
capture there; no socket is ever opened in CI.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import mailer
from app.main import app

client = TestClient(app)


def _contact(name: str, email: str) -> str:
    r = client.post(f"/clients/{name}/users", json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_unconfigured_smtp_is_503(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    uid = _contact("Rizerie Kindu", "dg@kindu.cd")
    r = client.post(f"/clients/Rizerie Kindu/users/{uid}/invite")
    assert r.status_code == 503
    assert "SMTP" in r.json()["detail"]


def test_invite_mails_a_working_absolute_link(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.cd")
    monkeypatch.setenv("SMTP_FROM", "bonjour@lumnia.cd")
    sent = []
    monkeypatch.setattr(mailer, "_deliver", lambda msg, cfg: sent.append(msg))
    uid = _contact("Café Ituri", "compta@ituri.cd")
    r = client.post(f"/clients/Café Ituri/users/{uid}/invite?lang=fr")
    assert r.status_code == 200, r.text
    assert r.json()["sent"] is True
    assert len(sent) == 1
    msg = sent[0]
    assert msg["To"] == "compta@ituri.cd"
    assert msg["From"] == "bonjour@lumnia.cd"
    assert "connexion" in msg["Subject"].lower()      # lang=fr honored
    body = msg.get_content()
    # the link is absolute and actually signs in
    link = next(l for l in body.split() if "/portal/login/" in l)
    assert link.startswith("http")
    path = link[link.index("/portal/login/"):]
    fresh = TestClient(app)
    assert fresh.get(path, follow_redirects=False).status_code == 303
    assert fresh.get("/portal/me").json()["email"] == "compta@ituri.cd"


def test_invite_for_foreign_contact_404s(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.cd")
    monkeypatch.setattr(mailer, "_deliver",
                        lambda msg, cfg: (_ for _ in ()).throw(
                            AssertionError("must not send")))
    _contact("Client Un", "un@un.cd")
    uid = _contact("Client Deux", "deux@deux.cd")
    assert client.post(
        f"/clients/Client Un/users/{uid}/invite").status_code == 404
