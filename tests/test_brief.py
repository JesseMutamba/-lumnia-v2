"""Phase 2: brief intake -> metric plan matching -> approval."""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _dated_book() -> bytes:
    rows = [["Date", "Zone", "Produit", "Montant", "Stock"]]
    for m in range(14):
        y, mo = 2025 + m // 12, m % 12 + 1
        for z, p in (("Nord", "Cacao"), ("Sud", "Cafe")):
            rows.append([f"{y}-{mo:02d}-10", z, p, 100 + m * 10, 30 - m])
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="VENTES", header=False, index=False,
                engine="openpyxl")
    return buf.getvalue()


def _an(content: bytes, name: str) -> dict:
    return client.post("/analyze", files={
        "file": (name, content, "application/octet-stream")}).json()


BRIEF = {
    "role": "Regional sales manager",
    "goals": ["Increase revenue", "Minimize loss from lack of inventory"],
    "questions": [
        "How is revenue trending?",
        "Which products are performing best/worst?",
        "Are products out of stock?",
        "What is the meaning of life?",           # unmatchable, honestly
    ],
    "cadence": "monthly", "lang": "en",
}


def test_brief_yields_plan_with_honest_statuses():
    an = _an(_dated_book(), "ventes.xlsx")
    r = client.post(f"/analyses/{an['id']}/brief", json=BRIEF)
    assert r.status_code == 200
    plan = r.json()["plan"]
    by_q = {q["question"]: q for q in plan["questions"]}

    trending = by_q["How is revenue trending?"]
    assert trending["status"] == "answerable"
    assert "trend" in trending["metrics"] and "mom" in trending["metrics"]

    stock = by_q["Are products out of stock?"]
    assert stock["status"] == "answerable"
    assert "low_stock" in stock["metrics"]

    assert by_q["What is the meaning of life?"]["status"] == "unmatched"

    # the brief + plan persist on the stored report
    stored = client.get(f"/analyses/{an['id']}").json()
    assert stored["brief"]["role"] == "Regional sales manager"
    assert stored["plan"]["approved"] is None


def test_unanswerable_question_names_requirements():
    # snapshot file: no dates, no stock
    df = pd.DataFrame({
        "Nom": [f"E{i}" for i in range(12)],
        "Secteur": ["A", "B", "C"] * 4,
        "Montant": [100 + i for i in range(12)],
    })
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="S", index=False, engine="openpyxl")
    an = _an(buf.getvalue(), "snap.xlsx")
    r = client.post(f"/analyses/{an['id']}/brief", json={
        "questions": ["Are products out of stock?"], "lang": "en"})
    q = r.json()["plan"]["questions"][0]
    assert q["status"] == "unanswerable"
    missing = {m["metric"]: m for m in q["missing"]}
    assert "stock_on_hand" in missing
    assert q["missing"][0]["metric"] == "stock_on_hand"   # specific leads
    assert "stock" in missing["stock_on_hand"]["requires"]


def test_approval_persists_and_filters_invalid_ids():
    an = _an(_dated_book(), "ventes2.xlsx")
    client.post(f"/analyses/{an['id']}/brief", json=BRIEF)
    r = client.post(f"/analyses/{an['id']}/plan",
                    json={"approved": ["trend", "mom", "not-a-metric"]})
    assert r.json()["approved"] == ["trend", "mom"]
    stored = client.get(f"/analyses/{an['id']}").json()
    assert stored["plan"]["approved"] == ["trend", "mom"]


def test_brief_survives_rerun_and_replans():
    an = _an(_dated_book(), "ventes3.xlsx")
    client.post(f"/analyses/{an['id']}/brief", json=BRIEF)
    client.post(f"/analyses/{an['id']}/plan", json={"approved": ["trend"]})
    rerun = client.post(f"/analyses/{an['id']}/rerun").json()
    assert rerun["brief"]["role"] == "Regional sales manager"
    assert rerun["plan"]["approved"] == ["trend"]


def test_brief_suggestion_from_same_client():
    a = _an(_dated_book(), "jan.xlsx")
    client.post(f"/analyses/{a['id']}/client", json={"client": "FERME K"})
    client.post(f"/analyses/{a['id']}/brief", json=BRIEF)
    # different bytes -> different analysis, same client
    b = _an(_dated_book() + b" ", "fev.xlsx")
    client.post(f"/analyses/{b['id']}/client", json={"client": "FERME K"})
    sug = client.get(f"/analyses/{b['id']}/brief-suggestion").json()
    assert sug["brief"]["role"] == "Regional sales manager"
    assert sug["from"] == "jan.xlsx"
    # no client, no suggestion — honestly empty
    c = _an(_dated_book() + b"  ", "solo.xlsx")
    assert client.get(f"/analyses/{c['id']}/brief-suggestion").json()["brief"] is None


def test_brief_requires_story_and_questions():
    df = pd.DataFrame([["A", "B"], ["x", "y"], ["z", "w"], ["q", "r"]])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="S", header=False, index=False, engine="openpyxl")
    an = _an(buf.getvalue(), "nostory.xlsx")
    assert client.post(f"/analyses/{an['id']}/brief",
                       json=BRIEF).status_code == 422
    an2 = _an(_dated_book() + b"   ", "noq.xlsx")
    assert client.post(f"/analyses/{an2['id']}/brief",
                       json={"questions": []}).status_code == 400
