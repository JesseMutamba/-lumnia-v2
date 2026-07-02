"""Chart-ready series computed at analysis time (dashboard payloads)."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.pipeline.charts import timeseries_chart, MAX_SERIES, MAX_PERIODS
from app.pipeline.orient import orient_sheet

from tests.fixtures import matrix_sheet, year_matrix_sheet


def _chart(df):
    t = orient_sheet(df)["tidy"]
    return (t.get("summary") or {}).get("chart")


# --------------------------------------------------------------------------- #
# timeseries (matrix sheets)
# --------------------------------------------------------------------------- #
def test_matrix_sheet_gets_timeseries_chart():
    c = _chart(matrix_sheet())
    assert c["kind"] == "timeseries"
    assert c["periods"] == ["2024-01-01", "2024-01-02", "2024-01-03"]
    by_label = {s["label"]: s for s in c["series"]}
    assert by_label["Regimes"]["values"] == [10, 20, 30]
    assert by_label["Tonnage"]["values"] == [5, 6, 7]
    assert c["other"] is None
    assert c["truncated"] is False


def test_year_matrix_chart_keeps_axis_order_and_gaps():
    c = _chart(year_matrix_sheet())
    assert c["kind"] == "timeseries"
    assert c["periods"] == ["2019-2021", 2025, 2026, 2027]
    hect = next(s for s in c["series"] if s["label"] == "HECTARES")
    assert hect["values"] == [475, 304, 800, 1200]
    prod = next(s for s in c["series"] if s["label"] == "PRODUCTION")
    assert prod["values"][0] is None          # blank cell stays a gap


def test_many_series_fold_into_other():
    n_labels = MAX_SERIES + 4
    rows = [["METRIC", dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 3)]]
    for k in range(n_labels):
        rows.append([f"metric {k}", k + 1, k + 1, k + 1])
    c = _chart(pd.DataFrame(rows))
    assert len(c["series"]) == MAX_SERIES
    assert c["other"] is not None
    assert c["other"]["label"] == "Other (4)"
    # Other = sum of the 4 smallest series (values 1..4 -> 10 per period)
    assert c["other"]["values"] == [10, 10, 10]
    # top series are the biggest by |total|
    assert c["series"][0]["label"] == f"metric {n_labels - 1}"


def test_timeseries_declines_when_nothing_numeric():
    assert timeseries_chart(["2024", "2025", "2026"], {"a": {}}) is None
    assert timeseries_chart([], {}) is None


def test_timeseries_truncates_honestly():
    periods = list(range(MAX_PERIODS + 50))
    vals = {i: 1.0 for i in range(MAX_PERIODS + 50)}
    c = timeseries_chart(periods, {"s": vals})
    assert c["truncated"] is True
    assert len(c["periods"]) == MAX_PERIODS
    assert len(c["series"][0]["values"]) == MAX_PERIODS


# --------------------------------------------------------------------------- #
# profile (tidy sheets with a numeric column run)
# --------------------------------------------------------------------------- #
def test_tidy_month_run_gets_profile_chart_excluding_totals():
    df = pd.DataFrame([
        ["Poste", "Jan", "Fev", "Mar", "Avr"],
        ["Salaires", 10, 20, 30, 40],
        ["Carburant", 5, 5, 5, 5],
        ["TOTAL", 15, 25, 35, 45],          # totals row must NOT inflate sums
    ])
    c = _chart(df)
    assert c["kind"] == "profile"
    assert c["x"] == ["Jan", "Fev", "Mar", "Avr"]
    assert c["y"] == [15, 25, 35, 45]
    assert c["excluded_totals"] == 1


def test_profile_drops_endpoint_total_column():
    df = pd.DataFrame([
        ["Poste", "Jan", "Fev", "Mar", "Total"],
        ["Salaires", 10, 20, 30, 60],
        ["Carburant", 5, 5, 5, 15],
        ["Entretien", 1, 2, 3, 6],
        ["Divers", 4, 3, 2, 9],
    ])
    c = _chart(df)
    assert c["x"] == ["Jan", "Fev", "Mar"]   # Total column not in the profile
    assert c["y"] == [20, 30, 40]


def test_no_chart_when_not_chartable():
    df = pd.DataFrame([                       # only 2 numeric columns: no run
        ["Nom", "A", "B"],
        ["x", 1, 2],
        ["y", 3, 4],
        ["z", 5, 6],
    ])
    assert _chart(df) is None
