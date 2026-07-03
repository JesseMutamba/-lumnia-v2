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
    short = [m.split(":", 1)[1] for m in trending["metrics"]]
    assert "trend" in short and "mom" in short

    stock = by_q["Are products out of stock?"]
    assert stock["status"] == "answerable"
    assert any(m.endswith(":low_stock") for m in stock["metrics"])

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
                    json={"approved": ["s0:trend", "s0:mom", "not-a-metric"]})
    assert r.json()["approved"] == ["s0:trend", "s0:mom"]
    stored = client.get(f"/analyses/{an['id']}").json()
    assert stored["plan"]["approved"] == ["s0:trend", "s0:mom"]


def test_brief_survives_rerun_and_replans():
    an = _an(_dated_book(), "ventes3.xlsx")
    client.post(f"/analyses/{an['id']}/brief", json=BRIEF)
    client.post(f"/analyses/{an['id']}/plan", json={"approved": ["s0:trend"]})
    rerun = client.post(f"/analyses/{an['id']}/rerun").json()
    assert rerun["brief"]["role"] == "Regional sales manager"
    assert rerun["plan"]["approved"] == ["s0:trend"]


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


def _matrix_book() -> bytes:
    """A PVAK-shaped cross-tab: date columns, series rows incl. a TOTAL
    (derived) and a STOCK series, on one sheet named for its content."""
    import datetime as dt
    days = [dt.date(2025, 7, 1) + dt.timedelta(days=i * 7) for i in range(10)]
    rows = [["SERIE"] + days]
    b19 = [10 + i for i in range(10)]
    b20 = [5 + 2 * i for i in range(10)]
    rows.append(["BLOC 2019"] + b19)
    rows.append(["BLOC 2020"] + b20)
    rows.append(["TOTAL MATURE"] + [a + b for a, b in zip(b19, b20)])
    rows.append(["STOCK GASOIL"] + [100 - 9 * i for i in range(10)])
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="PRODUCTION CHAMP", header=False,
                index=False, engine="openpyxl")
    return buf.getvalue()


def test_matrix_story_with_movers_and_stock():
    an = _an(_matrix_book(), "champ.xlsx")
    st = an["story"]
    assert st is not None and st["sheet"].strip() == "PRODUCTION CHAMP"
    ids = {m["id"] for m in st["metrics"]}
    assert {"headline", "trend", "mom", "by_Série", "movers", "low_stock"} <= ids
    # TOTAL series excluded: headline == sum of the two blocs only
    head = next(m for m in st["metrics"] if m["id"] == "headline")
    assert head["value"] == sum(10 + i for i in range(10)) + sum(5 + 2 * i for i in range(10))
    # movers never name the TOTAL series
    mv = next(m for m in st["metrics"] if m["id"] == "movers")
    names = {r["entity"] for r in mv["top"] + mv["bottom"]}
    assert "TOTAL MATURE" not in names and "BLOC 2020" in names
    # the declining stock series is tracked at its latest level
    ls = next(m for m in st["metrics"] if m["id"] == "low_stock")
    assert ls["rows"][0] == {"entity": "STOCK GASOIL", "value": 19.0}


def test_french_questions_route_to_matrix_story():
    an = _an(_matrix_book(), "champ2.xlsx")
    r = client.post(f"/analyses/{an['id']}/brief", json={
        "role": "Chef de plantation", "cadence": "monthly", "lang": "fr",
        "questions": [
            "Comment évolue la production mois par mois ?",
            "Quel bloc produit le plus, et lequel sous-performe ?",
            "Le stock de gasoil descend-il vers la rupture ?",
        ]})
    plan = r.json()["plan"]
    by_q = {q["question"][:20]: q for q in plan["questions"]}
    # 'partial' is honest here: trend + MoM computable, YoY needs 13 months
    trend_q = by_q["Comment évolue la pr"]
    assert trend_q["status"] in ("answerable", "partial")
    short = [m.split(":", 1)[1] for m in trend_q["metrics"]]
    assert "trend" in short and "mom" in short
    perf_q = by_q["Quel bloc produit le"]
    assert perf_q["status"] in ("answerable", "partial")
    assert any(m.endswith(":movers") for m in perf_q["metrics"])
    stock_q = by_q["Le stock de gasoil d"]
    assert stock_q["status"] == "answerable"
    assert any(m.endswith(":low_stock") for m in stock_q["metrics"])


def test_questions_can_land_on_different_sheets():
    """Two sheets, two stories: the stock question must route to the sheet
    that actually has stock data, not the spine."""
    import datetime as dt
    days = [dt.date(2025, 7, 1) + dt.timedelta(days=i * 7) for i in range(10)]
    prod = pd.DataFrame([["SERIE"] + days,
                         ["BLOC A"] + [10 + i for i in range(10)],
                         ["BLOC B"] + [8 + i for i in range(10)]])
    stock = pd.DataFrame([["SERIE"] + days,
                          ["STOCK GO"] + [50 - 4 * i for i in range(10)],
                          ["ENTREE GO"] + [5] * 10])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        prod.to_excel(xw, sheet_name="PRODUCTION", header=False, index=False)
        stock.to_excel(xw, sheet_name="CARBURANT", header=False, index=False)
    an = _an(buf.getvalue(), "twosheets.xlsx")
    assert len(an["stories"]) >= 2
    r = client.post(f"/analyses/{an['id']}/brief", json={
        "questions": ["Le stock de gasoil descend-il ?"], "lang": "fr"})
    q = r.json()["plan"]["questions"][0]
    assert q["status"] == "answerable"
    assert q["sheet"].strip() == "CARBURANT"
