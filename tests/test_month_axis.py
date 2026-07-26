"""Oracle: month-name period headers ("Jan 2026", "févr. 2026") are dates.

Real actuals sheets — including the anchor portfolio model — head their
month columns with TEXT like "Jan 2026" / "Février 2026", not date cells.
Those must classify and coerce as dates so the sheet becomes a date-axis
matrix, the monthly block tags roles, and monthly unit economics derive.
Aggregate columns ("Q1 Total") and ordinary labels ("Total 2026" has no
month word) must stay text — a fabricated period is worse than none.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.celltypes import cell_kind, DATE, TEXT
from app.pipeline.coerce import coerce_date

client = TestClient(app)


def test_month_year_strings_classify_and_coerce_as_dates():
    cases = {
        "Jan 2026": "2026-01-01",
        "Feb 2026": "2026-02-01",
        "Sept 2026": "2026-09-01",
        "janv. 2026": "2026-01-01",
        "Février 2026": "2026-02-01",
        "août 2026": "2026-08-01",
        "Décembre 2025": "2025-12-01",
        "mars-26": "2026-03-01",
        "JUIL 2026": "2026-07-01",
    }
    for raw, iso in cases.items():
        assert cell_kind(raw) == DATE, raw
        assert coerce_date(raw) == iso, raw


def test_non_months_never_become_dates():
    # "2026" alone is a NUMBER; the rest are labels — none may turn DATE
    for raw in ("Q1 Total", "Total 2026", "Budget 2026", "Metric",
                "Produit 2025", "2026", "Mai", "T1 2026"):
        assert cell_kind(raw) != DATE, raw


def _actuals_book(with_budget: bool = False) -> bytes:
    """The anchor shape: a metric column, month-name TEXT headers, a section
    band row, and a trailing quarter-total column that must not become a
    period. With ``with_budget``, a year-axis BUDGET sheet rides along so the
    actual-vs-budget unit-cost comparison can derive."""
    rows = [
        ["Actuals — Q1 2026", None, None, None, None],
        [None, None, None, None, None],
        ["Metric", "Jan 2026", "Feb 2026", "Mar 2026", "Q1 Total"],
        ["PRODUCTION", None, None, None, None],
        ["CPO produced (t)", 16, 17.5, 18.5, 52],
        ["FFB produced (t)", 76, 80, 84, 240],
        ["CASH OUT", None, None, None, None],
        ["OPEX — production cost (USD)", 14200, 16900, 15400, 46500],
        ["CAPEX — investment (USD)", 41000, 52500, 49800, 143300],
        # a derived RATE row, exactly like the real model ships — its big
        # dollar values must never out-tag the true tonnage rows
        ["Cost / tonne CPO (USD/t)", 887.5, 965.7, 832.4, None],
    ]
    budget = [
        ["Metric", 2025, 2026, 2027],
        ["FFB produced (t)", 3000, 3200, 4500],
        ["CPO produced (t)", 650, 700, 990],
        ["OPEX — production cost (USD)", 351000, 378000, 534600],
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="Actuals", header=False,
                                    index=False)
        if with_budget:
            pd.DataFrame(budget).to_excel(w, sheet_name="BUDGET 2025-2027",
                                          header=False, index=False)
    return buf.getvalue()


def test_actuals_sheet_lights_the_monthly_block():
    r = client.post("/analyze", files={
        "file": ("actuals.xlsx", _actuals_book(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert m is not None, "no model — monthly actuals did not tag"
    mo = m["monthly"]
    assert mo is not None, "monthly block absent"
    # exactly the three months — the Q1 Total column is NOT a period
    assert [p[:7] for p in mo["periods"]] == ["2026-01", "2026-02", "2026-03"]
    roles = set(mo["metrics"])
    assert {"volume", "volume_secondary", "opex"} <= roles, roles
    # the rate row is nobody's level series: volumes are the tonnage rows
    tagged = {r: mo["metrics"][r]["label"] for r in mo["metrics"]}
    assert tagged["volume"] == "FFB produced (t)", tagged
    assert tagged["volume_secondary"] == "CPO produced (t)", tagged
    assert not any("/ tonne" in l for l in tagged.values()), tagged
    # monthly unit economics derive: cost per tonne of output, per month
    out = mo["derived"]["opex_per_volume_out"]
    assert out[0] == round(14200 / 16, 4)
    assert out[2] == round(15400 / 18.5, 4)
    # no budget sheet in the workbook -> no vs-budget claim, honestly absent
    assert "unit_cost_budget" not in mo


def test_monthly_unit_cost_compares_against_the_budget_year():
    r = client.post("/analyze", files={
        "file": ("model.xlsx", _actuals_book(with_budget=True),
                 "application/octet-stream")})
    assert r.status_code == 200, r.text
    mo = r.json()["model"]["monthly"]
    ub = mo.get("unit_cost_budget")
    assert ub is not None, "vs-budget block missing"
    # target = the BUDGET sheet's own unit cost for the actuals' year
    assert ub["target"] == round(378000 / 700, 4)          # 540.0 for 2026
    assert ub["target_period"] == "2026"
    assert ub["basis"] == "opex_per_volume_out"
    # per-month variance, computed by the pipeline — never by the frontend
    assert ub["variance_pct"][0] == round((14200 / 16 - 540) / 540 * 100, 1)
    assert ub["variance_pct"][2] == round(
        (15400 / 18.5 - 540) / 540 * 100, 1)


# ---------------------------------------------------------------------------
# The banner-year rule: bare month headers ("Janvier"…) adopt a year ONLY
# when exactly one unambiguous 4-digit year appears in the table's own text
# (banner or labels). Zero years or two refuse — a fabricated period is
# worse than none. Stacked month-bands with different years split first.
# ---------------------------------------------------------------------------
MOIS = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin"]


def _one_band(banner: str) -> list:
    return [
        [banner, None, *MOIS],
        ["MWEBE", "BU", 87, 235, 336, 470, 336, 201],
        [None, "CPO Produits (T)", 20.01, 54.05, 77.28, 108.1, 77.28, 46.23],
        [None, "FFB (T)", 87, 235, 336, 470, 336, 201],
    ]


def _post(rows, name="plan.xlsx", sheet="PLAN PRODUCTION"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name=sheet, header=False,
                                    index=False)
    return client.post("/analyze", files={
        "file": (name, buf.getvalue(), "application/octet-stream")})


def test_bare_month_header_adopts_the_single_banner_year():
    r = _post(_one_band("ESTIMATION PRODUCTION 2026"))
    assert r.status_code == 200, r.text
    sheet = r.json()["sheets"][0]
    assert sheet["orientation"] == "matrix", sheet["orientation_reason"]
    ch = sheet["tidy"]["summary"]["chart"]
    assert ch["axis"] == "date"
    assert ch["periods"][0] == "2026-01-01"
    assert ch["periods"][5] == "2026-06-01"
    # provenance rides: the CPO row cites its real cells (row 3, C..H)
    ser = {s["label"]: s for s in ch["series_all"]}
    key = next(k for k in ser if "CPO Produits" in k)
    assert ser[key]["cells"][0] == ["C3"]


def test_ambiguous_or_missing_banner_year_refuses():
    # two year tokens in the table region -> unknowable, no date axis
    r2 = _post(_one_band("PRODUCTION 2025&2026"))
    s2 = r2.json()["sheets"][0]
    ch2 = ((s2.get("tidy") or {}).get("summary") or {}).get("chart") or {}
    assert not (ch2.get("kind") == "timeseries" and ch2.get("axis") == "date")
    # no year token anywhere -> same honest refusal
    r3 = _post(_one_band("ESTIMATION PRODUCTION"))
    s3 = r3.json()["sheets"][0]
    ch3 = ((s3.get("tidy") or {}).get("summary") or {}).get("chart") or {}
    assert not (ch3.get("kind") == "timeseries" and ch3.get("axis") == "date")


def test_stacked_month_bands_split_and_keep_their_own_years():
    # the anchor montage shape: a 2025 block, a blank row, a 2026 block —
    # one tidy read would be wrong; the bands split and each keeps its year
    rows = [[None, "PRODUCTION 2025", *MOIS],
            [None, "CPO 2025", 1, 2, 3, 4, 5, 6],
            [None, "FFB 2025", 5, 10, 15, 20, 25, 30],
            [None] * 8,
            ["ESTIMATION PRODUCTION 2026", None, *MOIS],
            ["MWEBE", "BU", 87, 235, 336, 470, 336, 201],
            [None, "CPO Produits (T)", 20.01, 54.05, 77.28, 108.1, 77.28,
             46.23],
            [None, "FFB (T)", 87, 235, 336, 470, 336, 201]]
    r = _post(rows, sheet="PRODUCTION 2025&2026")
    assert r.status_code == 200, r.text
    sheet = r.json()["sheets"][0]
    assert sheet["orientation"] == "multi", sheet["orientation_reason"]
    axes = []
    for p in sheet["panels"]:
        ch = ((p.get("tidy") or {}).get("summary") or {}).get("chart") or {}
        if ch.get("axis") == "date":
            axes.append(ch["periods"][0][:4])
    assert sorted(axes) == ["2025", "2026"]
