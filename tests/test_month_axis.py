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


def _actuals_book() -> bytes:
    """The anchor shape: a metric column, month-name TEXT headers, a section
    band row, and a trailing quarter-total column that must not become a
    period."""
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
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, sheet_name="Actuals", header=False,
                                index=False, engine="openpyxl")
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
