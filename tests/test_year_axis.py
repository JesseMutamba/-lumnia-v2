"""Phase 0 of the executive-insights spec: a tidy table with a bare Year
column must light the time sockets — story trend by year, a year-axis
timeseries chart, and (when roles tag) the business model."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.semantics import plan_from_brief

client = TestClient(app)

CSV = b"""Year,Revenue_USD,OPEX_USD,CPO_Produced_T,FFB_Harvested_T,Budget_OPEX_USD
2025,239000,193000,254,1187,180000
2026,771000,362000,771,3353,360000
2027,1080000,553000,1082,4704,520000
2028,1540000,716000,1539,6692,700000
2029,2820000,868000,2821,12266,860000
2030,5230000,997000,5228,22729,990000
"""


def _analyze() -> dict:
    return client.post("/analyze", files={
        "file": ("financial_timeseries_test.csv", CSV, "text/csv")}).json()


def test_year_column_becomes_the_time_axis():
    schema = _analyze()["story"]["schema"]  # story blob: sheet+schema+metrics
    t = schema["time"]
    assert t is not None
    assert t["name"] == "Year"
    assert t["grain"] == "year"
    assert t["n_periods"] == 6
    # the year column is the axis, never a quantity
    assert "Year" not in [m["name"] for m in schema["measures"]]


def test_story_trend_plots_revenue_by_year():
    story = _analyze()["story"]
    by_id = {m["id"]: m for m in story["metrics"]}

    trend = by_id["trend"]
    assert trend["measure"] == "Revenue_USD"
    assert trend["grain"] == "year"
    assert len(trend["rows"]) == 6
    assert trend["rows"][0] == {"period": "2025", "value": 239000}
    assert trend["rows"][-1] == {"period": "2030", "value": 5230000}

    # yearly grain: last-vs-previous IS year over year; MoM does not apply
    assert by_id["yoy"]["pct"] == round((5230000 / 2820000 - 1) * 100, 4)
    assert "mom" not in by_id
    gap_metrics = {g["metric"] for g in story["gaps"]}
    assert not {"trend", "mom_change", "yoy_change"} & gap_metrics


def test_tidy_year_chart_feeds_the_model():
    rep = _analyze()
    chart = rep["sheets"][0]["tidy"]["summary"]["chart"]
    assert chart["kind"] == "timeseries"
    assert chart["axis"] == "year"
    assert chart["periods"] == [2025, 2026, 2027, 2028, 2029, 2030]
    labels = {s["label"] for s in chart["series_all"]}
    assert "Revenue_USD" in labels and "Year" not in labels

    m = rep["model"]
    assert m is not None
    assert m["metrics"]["revenue"]["values"][-1] == 5230000
    # actual OPEX (largest total) wins over the budget line
    assert m["metrics"]["opex"]["label"] == "OPEX_USD"
    assert m["derived"]["margin_pct"][-1] == round(
        (5230000 - 997000) / 5230000 * 100, 4)
    assert m["scenario_ready"] is True


def test_trend_question_round_trips():
    rep = _analyze()
    plan = plan_from_brief({"questions": ["How is Revenue_USD trending?"]},
                           rep["stories"])
    q = plan["questions"][0]
    assert q["status"] == "answerable"
    assert any(mid.endswith(":trend") for mid in q["metrics"])
