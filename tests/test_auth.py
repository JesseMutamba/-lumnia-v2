"""Shared-password gate: off when unconfigured, enforced when set."""
from __future__ import annotations

import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures import tidy_sheet

client = TestClient(app)


def _xlsx() -> bytes:
    buf = io.BytesIO()
    tidy_sheet().to_excel(buf, sheet_name="TIDY", header=False, index=False,
                          engine="openpyxl")
    return buf.getvalue()


def test_no_password_means_open(monkeypatch):
    monkeypatch.delenv("LUMNIA_PASSWORD", raising=False)
    assert client.get("/").status_code == 200
    assert client.get("/analyses").status_code == 200


def test_gated_when_password_set(monkeypatch):
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    # health stays open (Fly probes it), the app does not
    assert client.get("/health").status_code == 200
    assert client.get("/analyses").status_code == 401
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code == 200


def test_wrong_password_rejected(monkeypatch):
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    r = client.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 401
    assert "Incorrect" in r.text


def test_login_then_access(monkeypatch):
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    with TestClient(app) as c:
        r = c.post("/login", data={"password": "s3cret"}, follow_redirects=False)
        assert r.status_code == 303
        assert c.get("/analyses").status_code == 200        # cookie carried
        upload = c.post("/analyze",
                        files={"file": ("a.xlsx", _xlsx(), "application/octet-stream")})
        assert upload.status_code == 200
        c.get("/logout")
        assert c.get("/analyses").status_code == 401         # session cleared


def test_token_tamper_rejected(monkeypatch):
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    from app import auth
    good = auth.make_token()
    assert auth.valid_token(good)
    issued = good.split(".", 1)[0]
    assert not auth.valid_token(f"{issued}.deadbeef")
    # a token minted under a different password must not validate
    monkeypatch.setenv("LUMNIA_PASSWORD", "different")
    assert not auth.valid_token(good)
