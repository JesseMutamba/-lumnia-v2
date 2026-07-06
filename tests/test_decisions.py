"""PR A oracle: stable finding ids + persisted per-finding decisions.

A finding's id must be deterministic (same relation -> same id across reads
and reruns) so a decision made today still points at the same problem
tomorrow. Decisions: open (default) / approved / flagged.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _book() -> bytes:
    """qty x unit = total with ONE broken row (5 x 8 = 1290, delta 1250)."""
    df = pd.DataFrame([
        ["Description", "Qté", "Cout unit", "Montant"],
        ["Semences", 100, 2.4, 240],
        ["Machettes", 5, 8, 1290],
        ["Pelles", 4, 8, 32],
        ["Limes", 10, 1.5, 15],
        ["Brouettes", 3, 45, 135],
        ["Sacs", 200, 0.25, 50],
    ])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="CAPEX", header=False, index=False,
                engine="openpyxl")
    return buf.getvalue()


def _an(salt: bytes) -> dict:
    """Analyze unique bytes (salt defeats the content-dedup cache)."""
    r = client.post("/analyze", files={
        "file": ("capex.xlsx", _book() + salt, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def _findings(aid: str) -> dict:
    r = client.get(f"/analyses/{aid}/findings")
    assert r.status_code == 200
    return r.json()


def _decide(aid: str, decisions: dict):
    return client.post(f"/analyses/{aid}/decisions",
                       json={"decisions": decisions})


def test_findings_carry_stable_ids_and_default_open():
    a = _an(b"a")
    first = _findings(a["id"])
    assert first["n_mismatched_relations"] >= 1
    for f in first["findings"] + first["verified"] + first["unverified"]:
        assert len(f["id"]) >= 12
        assert f["decision"] == "open"
    # deterministic: a second aggregation yields the identical ids, in order
    second = _findings(a["id"])
    assert [f["id"] for f in first["findings"]] == \
           [f["id"] for f in second["findings"]]


def test_decide_batch_persists_and_returns_counts():
    a = _an(b"b")
    fs = _findings(a["id"])
    fid = fs["findings"][0]["id"]
    r = _decide(a["id"], {fid: "approved"})
    assert r.status_code == 200
    counts = r.json()
    assert counts["approved"] == 1
    assert counts["open"] == (fs["n_mismatched_relations"]
                              + fs["n_unverified_relations"] - 1)
    # persisted: a fresh read carries the decision
    again = _findings(a["id"])
    assert next(f for f in again["findings"] if f["id"] == fid)["decision"] \
        == "approved"


def test_unknown_id_bad_value_and_missing_analysis_rejected():
    a = _an(b"c")
    assert _decide(a["id"], {"nope00000000": "approved"}).status_code == 400
    fid = _findings(a["id"])["findings"][0]["id"]
    assert _decide(a["id"], {fid: "maybe"}).status_code == 400
    assert _decide("absent", {}).status_code == 404
    # the two 400s changed nothing
    assert _findings(a["id"])["findings"][0]["decision"] == "open"


def test_rerun_preserves_decisions_for_surviving_ids():
    a = _an(b"d")
    fid = _findings(a["id"])["findings"][0]["id"]
    assert _decide(a["id"], {fid: "flagged"}).status_code == 200
    assert client.post(f"/analyses/{a['id']}/rerun").status_code == 200
    after = _findings(a["id"])
    hit = next(f for f in after["findings"] if f["id"] == fid)
    assert hit["decision"] == "flagged"


def test_open_findings_denormalized_on_listing():
    a = _an(b"e")
    fs = _findings(a["id"])
    total = fs["n_mismatched_relations"] + fs["n_unverified_relations"]
    meta = {m["id"]: m for m in client.get("/analyses").json()}[a["id"]]
    assert meta["open_findings"] == total
    fid = fs["findings"][0]["id"]
    _decide(a["id"], {fid: "approved"})
    meta = {m["id"]: m for m in client.get("/analyses").json()}[a["id"]]
    assert meta["open_findings"] == total - 1
