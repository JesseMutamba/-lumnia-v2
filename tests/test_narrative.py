"""Layer 3: the AI narrative — phrases verified numbers, never computes.

The Claude call itself is mocked everywhere: tests must not hit the network,
and the contract under test is ours (fact sheet in, validated JSON out,
honest failure modes), not the model's prose.
"""
from __future__ import annotations

import io
import json

import pandas as pd
from fastapi.testclient import TestClient

from app import narrative
from app.main import app

client = TestClient(app)


def _book() -> bytes:
    df = pd.DataFrame([
        [None, 2025, 2026, 2027, 2028],
        ["REVENUS", 100, 300, 500, 900],
        ["CHARGES OPEX", 60, 120, 200, 300],
    ])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="R", header=False, index=False, engine="openpyxl")
    return buf.getvalue()


def _analyzed_id() -> str:
    r = client.post("/analyze", files={
        "file": ("n.xlsx", _book(), "application/octet-stream")})
    return r.json()["id"]


def test_facts_carry_model_and_audit_numbers():
    r = client.post("/analyze", files={
        "file": ("n.xlsx", _book(), "application/octet-stream")}).json()
    facts = narrative.build_facts(r, {"n_mismatched_relations": 2,
                                      "total_abs_delta": 1691.0,
                                      "n_verified_relations": 7,
                                      "n_unverified_relations": 1})
    assert 'revenue = "REVENUS": 2025=100, 2026=300' in facts
    assert "derived margin" in facts
    assert "2 broken relations" in facts and "1,691" in facts


def test_narrative_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post(f"/analyses/{_analyzed_id()}/narrative")
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_narrative_generated_and_cached(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(narrative, "_call_claude", lambda facts: json.dumps({
        "headline": "Revenue tripled from 100 to 300.",
        "narrative": "Two paragraphs of honest prose.",
        "watchouts": ["OPEX doubled.", "x", "y", "z-too-many"],
    }))
    an_id = _analyzed_id()
    resp = client.post(f"/analyses/{an_id}/narrative")
    assert resp.status_code == 200
    body = resp.json()
    assert body["headline"].startswith("Revenue tripled")
    assert len(body["watchouts"]) == 3          # capped
    # cached with the stored report
    stored = client.get(f"/analyses/{an_id}").json()
    assert stored["narrative"]["headline"] == body["headline"]
    # ...and dropped on rerun: numbers may change, stale prose must not stay
    rerun = client.post(f"/analyses/{an_id}/rerun").json()
    assert rerun["narrative"] is None


def test_narrative_bad_model_output_is_502(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(narrative, "_call_claude",
                        lambda facts: "sorry, no json here")
    resp = client.post(f"/analyses/{_analyzed_id()}/narrative")
    assert resp.status_code == 502
    assert "not valid JSON" in resp.json()["detail"]
