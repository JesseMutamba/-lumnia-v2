"""Oracle: the public landing page at the root.

With a password configured, an anonymous visitor at / gets the marketing
page (public, bilingual, CTAs into /portal/signin) — never the app and
never a login wall. A signed-in operator still gets the workspace, and
dev mode (no password) keeps serving the app directly. The product shot
is public too.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

APP_MARKER = 'id="topbar"'          # the SPA shell
LANDING_MARKER = "/portal/signin"   # every landing CTA points here


def test_anonymous_visitor_gets_the_landing(monkeypatch):
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    fresh = TestClient(app)
    r = fresh.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert LANDING_MARKER in r.text
    assert APP_MARKER not in r.text
    assert "Espace client" in r.text          # FR leads
    assert "How it works" in r.text           # EN rides along


def test_signed_in_operator_still_gets_the_app(monkeypatch):
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    fresh = TestClient(app)
    assert fresh.post("/login", data={"password": "s3cret"},
                      follow_redirects=False).status_code == 303
    r = fresh.get("/", headers={"Accept": "text/html"})
    assert APP_MARKER in r.text


def test_dev_mode_serves_the_app(monkeypatch):
    monkeypatch.delenv("LUMNIA_PASSWORD", raising=False)
    r = client.get("/", headers={"Accept": "text/html"})
    assert APP_MARKER in r.text


def test_product_shot_is_public(monkeypatch):
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    fresh = TestClient(app)
    r = fresh.get("/landing-shot.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
