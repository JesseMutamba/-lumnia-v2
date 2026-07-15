"""Oracle: the generated Analysis Brief — a written, versioned hub deliverable.

The pipeline computes every figure (facts include the monthly actuals and the
vs-budget comparison); Claude only phrases the five fixed sections. The
rendered document is print-styled HTML stored as a kind='file' deliverable
for the analysis' client — versioned like any re-delivered file — and served
to the client through the existing signed-URL path, inline but script-dead
(CSP sandbox). Fail honest: no API key -> 503; no client -> 409; malformed
model output -> 502; nothing canned, nothing faked.
"""
from __future__ import annotations

import io
import json

import pandas as pd
from fastapi.testclient import TestClient

from app import narrative
from app.brief import build_brief_facts
from app.main import app

client = TestClient(app)

CANNED = {
    "title": "From raw records to a steered operation",
    "sections": {
        "engagement": "ENG-SECTION synthetic prose.",
        "cash": "CASH-SECTION synthetic prose.",
        "unit_cost": "UNITCOST-SECTION synthetic prose.",
        "trajectory": "TRAJ-SECTION synthetic prose.",
        "delivers": "DELIVERS-SECTION synthetic prose.",
    },
}


def _book() -> bytes:
    """Monthly actuals + a budget year sheet, so the brief's facts carry the
    vs-budget comparison (same anchor shape as test_month_axis)."""
    actuals = [
        ["Metric", "Jan 2026", "Feb 2026", "Mar 2026", "Q1 Total"],
        ["CPO produced (t)", 16, 17.5, 18.5, 52],
        ["FFB produced (t)", 76, 80, 84, 240],
        ["OPEX — production cost (USD)", 14200, 16900, 15400, 46500],
        ["CAPEX — investment (USD)", 41000, 52500, 49800, 143300],
    ]
    budget = [
        ["Metric", 2025, 2026, 2027],
        ["FFB produced (t)", 3000, 3200, 4500],
        ["CPO produced (t)", 650, 700, 990],
        ["OPEX — production cost (USD)", 351000, 378000, 534600],
        ["Revenue — gross (USD)", 682500, 735000, 1039500],
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(actuals).to_excel(w, sheet_name="Actuals", header=False,
                                       index=False)
        pd.DataFrame(budget).to_excel(w, sheet_name="Budget", header=False,
                                      index=False)
    return buf.getvalue()


def _analysis(salt: bytes, client_name: str | None = "Kivu Agro") -> str:
    r = client.post("/analyze", files={
        "file": ("model.xlsx", _book() + salt, "application/octet-stream")})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    if client_name:
        client.post(f"/analyses/{aid}/client", json={"client": client_name})
    return aid


def _hub_login(client_name: str, email: str) -> TestClient:
    r = client.post(f"/clients/{client_name}/users", json={"email": email})
    c = TestClient(app)
    assert c.get(r.json()["login_url"],
                 follow_redirects=False).status_code == 303
    return c


def test_brief_facts_carry_the_vs_budget_numbers():
    aid = _analysis(b"facts")
    r = client.get(f"/analyses/{aid}")
    facts = build_brief_facts(r.json(), None)
    assert "MONTHLY" in facts
    assert "540" in facts                      # the budget target, verbatim
    assert "unit cost vs BUDGET" in facts


def test_unavailable_without_key_503(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    aid = _analysis(b"nokey")
    assert client.post(f"/analyses/{aid}/brief-report").status_code == 503


def test_clientless_analysis_409(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    aid = _analysis(b"noclient", client_name=None)
    r = client.post(f"/analyses/{aid}/brief-report")
    assert r.status_code == 409
    assert "client" in r.json()["detail"].lower()


def test_brief_delivered_versioned_and_served_sandboxed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(narrative, "_call_claude",
                        lambda facts, lang="en", system=None:
                        json.dumps(CANNED))
    aid = _analysis(b"deliver")
    r = client.post(f"/analyses/{aid}/brief-report?lang=en")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1

    hub = _hub_login("Kivu Agro", "brief@kivu.cd")
    items = [d for d in hub.get("/portal/deliverables").json()
             if d["kind"] == "file"]
    assert len(items) == 1
    detail = hub.get(f"/portal/deliverables/{items[0]['id']}").json()
    served = client.get(detail["signed_url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/html")
    assert "inline" in served.headers["content-disposition"]
    assert served.headers["content-security-policy"] == "sandbox"
    html = served.text
    # Claude's prose is in; the deterministic figures are in; scripts are not
    assert "UNITCOST-SECTION" in html and "DELIVERS-SECTION" in html
    assert "540" in html                       # budget target, from the model
    assert "<script" not in html.lower()

    # regenerating bumps the version on the SAME deliverable
    r2 = client.post(f"/analyses/{aid}/brief-report?lang=en")
    assert r2.json()["version"] == 2
    items = [d for d in hub.get("/portal/deliverables").json()
             if d["kind"] == "file"]
    assert len(items) == 1 and items[0]["version"] == 2


def test_malformed_model_output_502(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(narrative, "_call_claude",
                        lambda facts, lang="en", system=None: "not json at all")
    aid = _analysis(b"badjson")
    assert client.post(f"/analyses/{aid}/brief-report").status_code == 502
