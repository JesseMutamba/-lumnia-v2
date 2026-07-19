"""Phase 3: the composed operations report.

One click assembles the pipeline's computed blocks (KPI strip, plan vs
actual, conversion, cash split, monthly table) into a self-contained,
script-free HTML deliverable in the client hub — versioned by title,
gated like publish (open findings must be decided), deterministic end to
end: no AI, no CDN, charts as inline SVG. Blocks the data cannot support
are skipped and DECLARED, never faked.
"""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MONTHS = [dt.date(2026, m, 28) for m in (1, 2, 3)]
CPO = [15.0, 15.4, 15.1]
PLAN_CPO = [20.0, 54.1, 77.3]
OPEX = [5795, 22719, 16109]
PLAN_OPEX = [33248, 33248, 33248]


def _book(with_plan=True) -> bytes:
    journal = pd.DataFrame([
        ["JOURNAL PRODUCTION USINE", None, None, None],
        ["SERIE", *MONTHS],
        ["PRODUCTION CPO (T)", *CPO],
        ["CHARGES OPEX SITE (USD)", *OPEX],
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        journal.to_excel(xw, sheet_name="JOURNAL", header=False, index=False)
        if with_plan:
            plan = pd.DataFrame([
                ["PLAN OPERATIONNEL 2026", None, None, None],
                ["SERIE", *MONTHS],
                ["PRODUCTION CPO PREVUE (T)", *PLAN_CPO],
                ["OPEX PREVISIONNEL (USD)", *PLAN_OPEX],
            ])
            plan.to_excel(xw, sheet_name="PROJECTIONS 2026", header=False,
                          index=False)
    return buf.getvalue()


def _analysis(with_plan=True, client_name="PVAK") -> str:
    r = client.post("/analyze", files={
        "file": ("pvak_t1.xlsx", _book(with_plan),
                 "application/octet-stream")})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    if client_name:
        assert client.post(f"/analyses/{aid}/client",
                           json={"client": client_name}).status_code == 200
    fs = client.get(f"/analyses/{aid}/findings").json()
    dec = {f["id"]: "approved" for f in fs["findings"]}
    dec.update({f["id"]: "flagged" for f in fs["unverified"]})
    if dec:
        client.post(f"/analyses/{aid}/decisions", json={"decisions": dec})
    return aid


def _hub_html(client_name: str, email: str, title_part: str) -> str:
    r = client.post(f"/clients/{client_name}/users", json={"email": email})
    hub = TestClient(app)
    assert hub.get(r.json()["login_url"],
                   follow_redirects=False).status_code == 303
    items = hub.get("/portal/deliverables").json()
    did = next(d["id"] for d in items if title_part in d["title"])
    url = hub.get(f"/portal/deliverables/{did}").json()["signed_url"]
    served = client.get(url)
    assert served.status_code == 200
    assert served.headers["Content-Security-Policy"] == \
        "sandbox allow-scripts"
    return served.text


def test_composed_report_is_versioned_scriptfree_and_deterministic():
    aid = _analysis()
    r = client.post(f"/analyses/{aid}/compose-report",
                    json={"blocks": [], "lang": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert "kpi" in body["blocks"] and "plan_vs_actual" in body["blocks"]
    assert "monthly_table" in body["blocks"]
    # cash needs capex; conversion needs a second volume — skipped, declared
    assert set(body["skipped"]) == {"cash", "conversion"}

    html = _hub_html("PVAK", "rapport@pvak.cd", "Operations report")
    assert "<script" not in html.lower()          # self-contained, script-free
    assert "30.1" in html                          # attainment, computed
    assert "44.7" in html
    assert "<svg" in html                          # charts are inline SVG
    assert "cdn." not in html and "http://" not in html \
        and 'src="https://' not in html            # no external fetches

    # recompose bumps the version on the SAME deliverable
    r2 = client.post(f"/analyses/{aid}/compose-report",
                     json={"blocks": [], "lang": "en"})
    assert r2.json()["version"] == 2


def test_composed_report_speaks_french():
    aid = _analysis(client_name="KIVU")
    r = client.post(f"/analyses/{aid}/compose-report",
                    json={"blocks": [], "lang": "fr"})
    assert r.status_code == 200, r.text
    html = _hub_html("KIVU", "fr@kivu.cd", "Rapport d'exploitation")
    assert "Plan vs réel" in html
    assert "30,1" in html                          # French decimal comma


def test_compose_requires_a_client():
    aid = _analysis(client_name=None)
    r = client.post(f"/analyses/{aid}/compose-report",
                    json={"blocks": [], "lang": "en"})
    assert r.status_code == 409


def test_compose_is_gated_on_open_findings():
    """A planted totals-row mismatch leaves an open finding: the composed
    report is refused exactly like publish — unreviewed numbers do not
    circulate."""
    bad = pd.DataFrame([
        ["Description", "Qté", "Cout unit", "Montant"],
        ["Semences", 100, 2.4, 240],
        ["Machettes", 5, 8, 400],                  # planted: should be 40
        ["Pelles", 4, 8, 32],
        ["Limes", 10, 1.5, 15],
        ["Brouettes", 3, 45, 135],
        ["Sacs", 200, 0.25, 50],
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        bad.to_excel(xw, sheet_name="USINE", header=False, index=False)
    aid = client.post("/analyze", files={
        "file": ("bad.xlsx", buf.getvalue(),
                 "application/octet-stream")}).json()["id"]
    assert client.post(f"/analyses/{aid}/client",
                       json={"client": "PVAK"}).status_code == 200
    r = client.post(f"/analyses/{aid}/compose-report",
                    json={"blocks": [], "lang": "en"})
    assert r.status_code == 409
    assert "open" in r.json()["detail"]


def test_unknown_and_uncomputable_blocks_are_refused():
    aid = _analysis()
    assert client.post(f"/analyses/{aid}/compose-report",
                       json={"blocks": ["pie_chart"], "lang": "en"}
                       ).status_code == 422
    # a real block the data cannot support: nothing to deliver -> refusal
    assert client.post(f"/analyses/{aid}/compose-report",
                       json={"blocks": ["cash"], "lang": "en"}
                       ).status_code == 422


def test_error_details_and_month_labels_speak_both_languages():
    """Errors surface raw in a bilingual UI — they carry both languages
    (EN · FR). Month labels in the composed report read as months, not
    ISO dates."""
    # gate errors are bilingual
    aid = _analysis(client_name=None)
    r = client.post(f"/analyses/{aid}/compose-report",
                    json={"blocks": [], "lang": "en"})
    assert r.status_code == 409
    assert " · " in r.json()["detail"]
    assert "Rattachez" in r.json()["detail"]
    # upload errors too
    r2 = client.post("/analyze", files={
        "file": ("vide.xlsx", b"", "application/octet-stream")})
    assert "Fichier vide" in r2.json()["detail"]
    # month labels: janv. 2026 / Jan 2026, never 2026-01
    aid2 = _analysis(client_name="MWENGA")
    client.post(f"/analyses/{aid2}/compose-report",
                json={"blocks": [], "lang": "fr"})
    html_fr = _hub_html("MWENGA", "mois@mwenga.cd", "Rapport d'exploitation")
    assert "janv. 2026" in html_fr and "2026-01" not in html_fr
