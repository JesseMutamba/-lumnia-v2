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
    # blocks the data can't support are skipped, declared: no capex (cash),
    # no second volume (conversion), plan covers no extra months (outlook),
    # no yearly model (net_cash), no line items (breakdowns)
    assert set(body["skipped"]) == {"plan_progress", "cash", "conversion",
                                    "outlook", "net_cash", "breakdowns"}

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


def test_plan_progress_block_renders_pace_and_gaps():
    """The composed report carries the plan-progress block verbatim from
    the computed model: % of plan year, declared expectation + phasing,
    pace on the exec card's thresholds, source named, gaps declared."""
    from app import compose
    report = {
        "filename": "demo.xlsx", "n_sheets": 2, "sheets": [],
        "model": {
            "periods": ["2025", "2026"], "source_sheet": "PLAN · RECAP",
            "metrics": {"opex": {"label": "SOUS-TOTAL OPEX",
                                 "values": [84000, 130000]}},
            "derived": {},
            "plan_progress": {
                "year": "2025",
                "roles": {"opex": {
                    "source": "journal", "window": ["2025-01", "2025-03"],
                    "n_months": 3, "actual_to_date": 31581.0,
                    "plan_year": 84000, "pct_of_year": 37.6,
                    "expected_pct": 24.7, "phasing": "monthly_plan",
                    "plan_sources": {}}},
                "gaps": [{"role": "volume",
                          "reason": "nothing in the actuals measures this "
                                    "role monthly",
                          "requires": "monthly volume actuals for 2025"}],
            },
        },
    }
    assert "plan_progress" in compose.available_blocks(report)
    html = compose.render_report_html(report, None, "Demo", "en",
                                      ["plan_progress"])
    assert "Progress vs plan 2025" in html
    assert "37.6%" in html and "24.7%" in html
    assert "over pace" in html                  # 37.6 > 24.7 × 1.25
    assert "verified journal" in html
    assert "monthly volume actuals for 2025" in html
    # no computed block -> honestly unavailable, never an empty section
    assert "plan_progress" not in compose.available_blocks({"model": {}})


def test_report_shell_brand_fonts_folding_and_hover():
    # The deliverable must be brand-correct OFFLINE: fonts travel inside
    # the file as data URIs (never a CDN link), sections fold with native
    # <details> (open by default — nothing hidden, prints intact), and
    # chart bars carry baked hover readouts + <title> fallbacks. All of it
    # with ZERO scripts and zero external fetches, same as ever.
    aid = _analysis()
    r = client.post(f"/analyses/{aid}/compose-report",
                    json={"blocks": [], "lang": "en"})
    assert r.status_code == 200, r.text
    html = _hub_html("PVAK", "fonts@pvak.cd", "Operations report")
    assert "@font-face" in html
    assert "Source Serif 4" in html and "IBM Plex Mono" in html
    assert "data:font/woff2;base64," in html
    assert '<details class="blk" open' in html
    assert "<summary" in html
    assert 'class="tt"' in html                    # CSS-only hover readouts
    assert "<title>" in html                       # native tooltip fallback
    assert "<script" not in html.lower()
    assert "https://" not in html and "http://" not in html


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


def _full_book() -> bytes:
    """The PVAK shape end to end: 3-month journal (FFB+CPO+OPEX), 12-month
    plan, yearly projections, and a line-item cost sheet."""
    m3 = MONTHS
    m12 = [dt.date(2026, m, 28) for m in range(1, 13)]
    plan_cpo = [20.0, 54.1, 77.3, 108.1, 77.3, 46.2,
                25.3, 54.1, 77.3, 108.1, 77.3, 46.2]      # sums 771.4
    journal = pd.DataFrame([
        ["JOURNAL PRODUCTION USINE", None, None, None],
        ["SERIE", *m3],
        ["RECOLTE FFB (T)", 71, 73, 67],
        ["PRODUCTION CPO (T)", *CPO],
        ["CHARGES OPEX SITE (USD)", *OPEX],
    ])
    plan = pd.DataFrame([
        ["PLAN OPERATIONNEL 2026", *([None] * 12)],
        ["SERIE", *m12],
        ["PRODUCTION CPO PREVUE (T)", *plan_cpo],
        ["OPEX PREVISIONNEL (USD)", *([33248] * 12)],
    ])
    recap = pd.DataFrame([
        [None, 2026, 2027, 2028, 2029],
        ["REVENUS BRUTS", 100, 200, 400, 800],
        ["DEPENSES OPEX", 60, 90, 150, 240],
        ["INVESTISSEMENTS", 500, 300, 100, 50],
    ])
    costs = pd.DataFrame([
        ["Description", "Unité", "Qté", "Montant"],
        ["Semences", "nbre", 10, 500],
        ["Machettes", "pièce", 5, 40],
        ["Engrais", "sac", 8, 240],
        ["TOTAL", None, 23, 780],
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        journal.to_excel(xw, sheet_name="JOURNAL", header=False, index=False)
        plan.to_excel(xw, sheet_name="PROJECTIONS 2026", header=False,
                      index=False)
        recap.to_excel(xw, sheet_name="RECAP", header=False, index=False)
        costs.to_excel(xw, sheet_name="COSTS", header=False, index=False)
    return buf.getvalue()


def test_v2_blocks_render_the_reference_story():
    """The full PVAK pack in one workbook: blended $/t with the phased-plan
    reference, the rest-of-year required run-rate, the yearly net balance
    with breakeven, and ranked line items — every number planted."""
    r = client.post("/analyze", files={
        "file": ("PVAK_pack.xlsx", _full_book(),
                 "application/octet-stream")})
    aid = r.json()["id"]
    assert client.post(f"/analyses/{aid}/client",
                       json={"client": "PVAK"}).status_code == 200
    fs = client.get(f"/analyses/{aid}/findings").json()
    dec = {f["id"]: "approved" for f in fs["findings"]}
    dec.update({f["id"]: "flagged" for f in fs["unverified"]})
    if dec:
        client.post(f"/analyses/{aid}/decisions", json={"decisions": dec})
    rep = client.post(f"/analyses/{aid}/compose-report",
                      json={"blocks": [], "lang": "en"})
    assert rep.status_code == 200, rep.text
    body = rep.json()
    for b in ("unit_cost", "outlook", "net_cash", "breakdowns"):
        assert b in body["blocks"], (b, body)
    html = _hub_html("PVAK", "pack@pvak.cd", "Operations report")
    assert "981" in html               # blended 44,623 / 45.5 t
    assert "659" in html               # phased plan 99,744 / 151.4 t
    assert "80.6" in html              # (771.3 - 45.5) / 9 remaining months
    # cumulative walk: -460, -650, -500, +10 -> positive in 2029; the
    # deepest deficit crossed is the 650 capital bridge
    assert "breakeven 2029" in html
    assert "capital bridge 650" in html
    assert "Semences" in html          # ranked line items
    assert "<script" not in html.lower()
