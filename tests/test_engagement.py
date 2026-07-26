"""Oracle: cross-workbook consolidation — actuals + projections in one shot.

Two files upload together; the server merges them into ONE engagement
workbook (plan sheets prefixed ``PLAN · ``), analyzes and stores the merged
bytes. The existing plan-pool machinery then delivers actuals-vs-projections
with zero model changes, and the trace endpoint resolves plan-sheet refs
from the stored merged bytes — the provenance chain stays byte-real.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _actuals_book() -> bytes:
    # monthly actuals, Q1 2026 — FFB in, CPO out, opex (test_month_axis shape)
    rows = [
        ["SUIVI MENSUEL 2026", None, None, None],
        ["RUBRIQUE", "Jan 2026", "Feb 2026", "Mar 2026"],
        ["CPO produced (t)", 16, 17.5, 18.5],
        ["FFB produced (t)", 76, 80, 84],
        ["OPEX — production cost (USD)", 14200, 16900, 15400],
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="EXTRACTION MOIS",
                                    header=False, index=False)
    return buf.getvalue()


def _plan_book() -> bytes:
    # the projections file: a year cross-tab named RECAP — NOT plan-shaped
    # by itself; the PLAN · prefix is what puts it in the plan pool
    rows = [
        ["Metric", 2025, 2026, 2027],
        ["FFB produced (t)", 3000, 3200, 4500],
        ["CPO produced (t)", 650, 700, 990],       # row 3 -> C3 for 2026
        ["OPEX — production cost (USD)", 351000, 378000, 534600],  # row 4
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="RECAP", header=False,
                                    index=False)
    return buf.getvalue()


def _post_pair():
    return client.post("/analyze", files={
        "file": ("suivi_2026.xlsx", _actuals_book(),
                 "application/octet-stream"),
        "plan": ("montage.xlsx", _plan_book(), "application/octet-stream")})


def test_engagement_upload_merges_and_compares():
    r = _post_pair()
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["filename"] == "suivi_2026 + plan.xlsx"
    names = [s["name"] for s in rep["sheets"]]
    assert names == ["EXTRACTION MOIS", "PLAN · RECAP"]

    # the projections sheet became the plan-shaped year spine…
    m = rep["model"]
    assert m["source_sheet"] == "PLAN · RECAP"
    # …and the cross-FILE comparison derives: monthly actuals vs plan-year
    # budget, through machinery that never learned about files
    ub = m["monthly"]["unit_cost_budget"]
    assert ub["target"] == round(378000 / 700, 4)
    assert ub["target_period"] == "2026"
    src = ub["target_sources"]
    assert src["opex"]["sheet"] == "PLAN · RECAP"
    assert src["opex"]["cells"] == ["C4"]

    # provenance stays byte-real: the trace endpoint re-reads the STORED
    # merged workbook at the plan sheet's cited cell
    t = client.get(f"/analyses/{rep['id']}/cells",
                   params={"sheet": "PLAN · RECAP", "refs": "C4,C3"})
    assert t.status_code == 200, t.text
    by_ref = {c["ref"]: c["value"] for c in t.json()["cells"]}
    assert by_ref["C4"] == 378000 and by_ref["C3"] == 700


def test_engagement_upload_is_deterministic():
    first = _post_pair().json()
    second = _post_pair().json()
    assert second["id"] == first["id"]      # same pair -> same analysis
    assert len(client.get("/analyses").json()) == 1


def test_single_file_upload_is_unchanged():
    r = client.post("/analyze", files={
        "file": ("solo.xlsx", _actuals_book(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["filename"] == "solo.xlsx"
    assert [s["name"] for s in rep["sheets"]] == ["EXTRACTION MOIS"]
    assert "unit_cost_budget" not in (rep["model"]["monthly"] or {})


def test_personnel_rows_never_tag_as_volume():
    # Cost-center sheets carry rows like "Personnel Production / P30a" —
    # payroll, in currency. "production" matches the volume pattern, but a
    # person is never a tonne: the headcount role must claim it first,
    # leaving volume to the real tonnage rows.
    import datetime as dt
    months = [dt.date(2026, m, 28) for m in (1, 2, 3)]
    rows = [["EXTRACTION", None, None, None],
            ["RUBRIQUE", *months],
            ["Récolte FFB (T) / P10a", 76, 80, 84],
            ["Personnel Production / P30a", 900000, 950000, 1132000],
            ["Personnel Maintenance / P30b", 300000, 320000, 360000]]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="EXTRACTION MOIS",
                                    header=False, index=False)
    r = client.post("/analyze", files={
        "file": ("extraction.xlsx", buf.getvalue(),
                 "application/octet-stream")})
    met = r.json()["model"]["monthly"]["metrics"]
    assert met["volume"]["label"] == "Récolte FFB (T) / P10a"
    assert "volume_secondary" not in met
    assert met["headcount"]["label"].startswith("Personnel")


def test_wide_matrices_reach_the_model():
    # 40 label rows — over the old 24-series ceiling — must still expose
    # series_all so the model (and mapping) can see the sheet at all.
    import datetime as dt
    months = [dt.date(2026, m, 28) for m in (1, 2, 3)]
    rows = [["JOURNAL LARGE", None, None, None],
            ["RUBRIQUE", *months]]
    rows += [[f"POSTE {k:02d} / C{k}", k, k + 1, k + 2] for k in range(1, 40)]
    rows.append(["PRODUCTION CPO (T)", 16, 17.5, 18.5])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="LARGE", header=False,
                                    index=False)
    r = client.post("/analyze", files={
        "file": ("large.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    rep = r.json()
    ch = rep["sheets"][0]["tidy"]["summary"]["chart"]
    assert len(ch["series_all"]) == 40
    # and the buried role series is now visible to the model layer
    assert rep["model"]["monthly"]["metrics"]["volume"]["label"] \
        == "PRODUCTION CPO (T)"
