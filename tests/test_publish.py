"""PR B oracle: publish lifecycle + exec-only snapshot + share-leak fix.

Publishing freezes an executive-only snapshot behind a stable token. The
gate: nothing publishes while a finding is still open. The guarantee: the
analyst-only material is never IN the public payload, so the public path
cannot leak it.
"""
from __future__ import annotations

import io
import json

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# keys that must never appear anywhere in a snapshot or the public payload
ANALYST_ONLY_KEYS = {"sheets", "findings", "mismatches", "missingness",
                     "preview", "records", "eda", "content"}


def _book() -> bytes:
    """One broken qty x unit = total row (delta 1250) + a dated column so
    the snapshot has a story chart to freeze."""
    df = pd.DataFrame([
        ["Date", "Description", "Qté", "Cout unit", "Montant"],
        ["2025-01-15", "Semences", 100, 2.4, 240],
        ["2025-02-15", "Machettes", 5, 8, 1290],   # should be 40
        ["2025-03-15", "Pelles", 4, 8, 32],
        ["2025-04-15", "Limes", 10, 1.5, 15],
        ["2025-05-15", "Brouettes", 3, 45, 135],
        ["2025-06-15", "Sacs", 200, 0.25, 50],
    ])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="CAPEX", header=False, index=False,
                engine="openpyxl")
    return buf.getvalue()


def _an(salt: bytes) -> dict:
    r = client.post("/analyze", files={
        "file": ("capex.xlsx", _book() + salt, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def _decide_all(aid: str) -> None:
    fs = client.get(f"/analyses/{aid}/findings").json()
    decisions = {f["id"]: "approved" for f in fs["findings"]}
    decisions.update({f["id"]: "flagged" for f in fs["unverified"]})
    if decisions:
        r = client.post(f"/analyses/{aid}/decisions",
                        json={"decisions": decisions})
        assert r.status_code == 200, r.text


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def test_publish_gated_until_every_finding_is_decided():
    a = _an(b"a")
    r = client.post(f"/analyses/{a['id']}/publish")
    assert r.status_code == 409
    assert "open" in r.json()["detail"]
    _decide_all(a["id"])
    r = client.post(f"/analyses/{a['id']}/publish")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["url"] == f"/published/{body['token']}"


def test_snapshot_is_exec_only_no_analyst_leakage():
    a = _an(b"b")
    _decide_all(a["id"])
    token = client.post(f"/analyses/{a['id']}/publish").json()["token"]
    r = client.get(f"/published/{token}")
    assert r.status_code == 200
    snap = r.json()
    # aggregates and the story chart are in; analyst material is NOT
    assert snap["filename"].startswith("capex")
    assert snap["verdict"]["mismatched"] == 1
    keys = set(_walk_keys(snap))
    assert not keys & ANALYST_ONLY_KEYS, keys & ANALYST_ONLY_KEYS
    # per-cell mismatch detail must be absent: the broken row's label
    assert "Machettes" not in json.dumps(snap, ensure_ascii=False)


def _model_book() -> bytes:
    """A year cross-tab (lights the business model) PLUS a cost line-item
    sheet whose labels (e.g. 'Machettes') must NOT reach the public snapshot
    — the published dashboard freezes aggregates, never raw line items."""
    recap = pd.DataFrame([
        [None, 2025, 2026, 2027],
        ["REVENUES BRUTS", 200, 400, 800],
        ["DEPENSES OPEX", 90, 150, 240],
        ["HECTARES", 50, 80, 120],
    ])
    costs = pd.DataFrame([
        ["Description", "Qté", "Montant"],
        ["Machettes", 5, 40],
        ["Pelles", 4, 32],
        ["TOTAL", 9, 72],
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        recap.to_excel(xw, sheet_name="RECAP", header=False, index=False)
        costs.to_excel(xw, sheet_name="COSTS", header=False, index=False)
    return buf.getvalue()


def test_snapshot_carries_model_but_not_raw_line_items():
    r = client.post("/analyze", files={
        "file": ("montage.xlsx", _model_book() + b"m", "application/octet-stream")})
    aid = r.json()["id"]
    _decide_all(aid)
    token = client.post(f"/analyses/{aid}/publish").json()["token"]
    snap = client.get(f"/published/{token}").json()
    # the aggregate business model is frozen so the public page renders it
    assert snap["model"] is not None
    assert snap["model"]["metrics"]["revenue"]["values"] == [200, 400, 800]
    assert snap["model"]["derived"]["margin_pct"]
    # audit counts ride along for the trust chip / verdict
    assert "n_mismatched_relations" in snap["audit"]
    # raw line items are stripped: no breakdowns key, and no item label leaks
    assert "breakdowns" not in snap["model"]
    assert "Machettes" not in json.dumps(snap, ensure_ascii=False)
    # the exec-only guarantee still holds
    assert not set(_walk_keys(snap)) & ANALYST_ONLY_KEYS


def test_old_share_endpoints_are_gone():
    a = _an(b"c")
    assert client.post(f"/analyses/{a['id']}/share").status_code == 404
    assert client.get("/share/sometoken/report").status_code == 404
    assert client.get("/share/sometoken/findings").status_code == 404


def test_republish_bumps_version_token_stable():
    a = _an(b"d")
    _decide_all(a["id"])
    one = client.post(f"/analyses/{a['id']}/publish").json()
    two = client.post(f"/analyses/{a['id']}/publish").json()
    assert (one["version"], two["version"]) == (1, 2)
    assert one["token"] == two["token"]


def test_stale_flips_on_new_decision_and_clears_on_republish():
    a = _an(b"e")
    _decide_all(a["id"])
    client.post(f"/analyses/{a['id']}/publish")
    meta = {m["id"]: m for m in client.get("/analyses").json()}[a["id"]]
    assert meta["published_version"] == 1 and meta["stale"] is False
    # a decision after publishing makes the published copy stale
    fid = client.get(f"/analyses/{a['id']}/findings").json()["findings"][0]["id"]
    client.post(f"/analyses/{a['id']}/decisions",
                json={"decisions": {fid: "flagged"}})
    meta = {m["id"]: m for m in client.get("/analyses").json()}[a["id"]]
    assert meta["stale"] is True
    client.post(f"/analyses/{a['id']}/publish")
    meta = {m["id"]: m for m in client.get("/analyses").json()}[a["id"]]
    assert meta["published_version"] == 2 and meta["stale"] is False


def test_opened_count_increments_per_public_read():
    a = _an(b"f")
    _decide_all(a["id"])
    token = client.post(f"/analyses/{a['id']}/publish").json()["token"]
    client.get(f"/published/{token}")
    client.get(f"/published/{token}")
    info = client.get(f"/analyses/{a['id']}/publish").json()
    assert info["opened_count"] == 2


def test_revoke_kills_the_link_immediately():
    a = _an(b"g")
    _decide_all(a["id"])
    token = client.post(f"/analyses/{a['id']}/publish").json()["token"]
    assert client.delete(f"/analyses/{a['id']}/publish").json()["revoked"] is True
    assert client.get(f"/published/{token}").status_code == 404
    assert client.get(f"/published/{token}/page").status_code == 404


def test_published_route_bypasses_password(monkeypatch):
    a = _an(b"h")
    _decide_all(a["id"])
    token = client.post(f"/analyses/{a['id']}/publish").json()["token"]
    monkeypatch.setenv("LUMNIA_PASSWORD", "s3cret")
    assert client.get("/analyses").status_code == 401         # app stays gated
    assert client.get(f"/published/{token}").status_code == 200
    assert client.get(f"/published/{token}/page").status_code == 200
