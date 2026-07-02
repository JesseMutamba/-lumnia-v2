"""Step 3: column statistics + table summaries over the extracted tables."""
from __future__ import annotations

import pandas as pd

from app.pipeline.metrics import ColumnAcc, DISTINCT_CAP
from app.pipeline.celltypes import NUMBER, TEXT
from app.pipeline.orient import orient_sheet

from tests.fixtures import (
    tidy_sheet,
    matrix_sheet,
    french_numbers_sheet,
)


def _col(tidy, name):
    return next(c for c in tidy["column_types"] if c["name"] == name)


# --------------------------------------------------------------------------- #
# Tidy tables
# --------------------------------------------------------------------------- #
def test_tidy_numeric_column_stats():
    tidy = orient_sheet(tidy_sheet())["tidy"]
    ha = _col(tidy, "HA")["stats"]["number"]     # values: 10, 12, 8
    assert ha["n"] == 3
    assert ha["min"] == 8
    assert ha["max"] == 12
    assert ha["sum"] == 30
    assert ha["mean"] == 10
    assert ha["zeros"] == 0 and ha["negatives"] == 0


def test_tidy_text_column_stats_after_ffill():
    tidy = orient_sheet(tidy_sheet())["tidy"]
    # PARCELLE is merged: BLOC 2019 spans two rows -> ffill makes count 2.
    txt = _col(tidy, "PARCELLE")["stats"]["text"]
    assert txt["distinct"] == 2
    assert txt["top"][0] == {"value": "BLOC 2019", "count": 2}


def test_tidy_table_summary():
    tidy = orient_sheet(tidy_sheet())["tidy"]
    s = tidy["summary"]
    assert s["n_records"] == 3
    assert s["n_columns"] == 4
    assert s["cell_fill"] == 1.0            # every cell filled after ffill
    assert s["numeric_columns"] == 3        # ANNEE, HA, REGIMES
    assert s["text_columns"] == 1           # PARCELLE


def test_french_numbers_feed_real_stats():
    tidy = orient_sheet(french_numbers_sheet())["tidy"]
    rend = _col(tidy, "RENDEMENT")["stats"]["number"]
    assert rend["sum"] == 3234.5            # "1 234,5" + "2 000"
    taux = _col(tidy, "TAUX")["stats"]["number"]
    assert taux["max"] == 12                # "12%" keeps face value


# --------------------------------------------------------------------------- #
# Matrix tables
# --------------------------------------------------------------------------- #
def test_matrix_value_stats_and_period_range():
    tidy = orient_sheet(matrix_sheet())["tidy"]
    val = _col(tidy, "value")["stats"]["number"]
    assert val["sum"] == 78                 # 10+20+30 + 5+6+7
    assert val["n"] == 6

    s = tidy["summary"]
    assert s["n_series"] == 2               # Regimes, Tonnage
    assert s["n_periods"] == 3
    assert s["period_min"] == "2024-01-01"
    assert s["period_max"] == "2024-01-03"


def test_matrix_period_column_is_dated():
    tidy = orient_sheet(matrix_sheet())["tidy"]
    per = _col(tidy, "period")
    assert per["dtype"] == "date"
    assert per["stats"]["date"]["min"] == "2024-01-01"
    assert per["stats"]["date"]["max"] == "2024-01-03"


# --------------------------------------------------------------------------- #
# Honesty guarantees
# --------------------------------------------------------------------------- #
def test_mixed_column_reports_per_kind_stats():
    df = pd.DataFrame([
        ["K", "NOM", "V"],
        ["a", "x", 10],
        ["b", "y", "haut"],
        ["c", "z", 20],
        ["d", "w", "bas"],
    ])
    tidy = orient_sheet(df)["tidy"]
    col = _col(tidy, "V")
    assert col["dtype"] == "mixed"
    # Numeric stats cover ONLY the numeric cells — explicitly n=2.
    assert col["stats"]["number"]["n"] == 2
    assert col["stats"]["number"]["sum"] == 30
    assert col["stats"]["text"]["n"] == 2


def test_blank_and_error_cells_counted_as_missing():
    df = pd.DataFrame([
        ["K", "NOM", "V"],
        ["a", "x", 10],
        ["b", "y", "#DIV/0!"],
        ["c", "z", None],
    ])
    tidy = orient_sheet(df)["tidy"]
    col = _col(tidy, "V")
    assert col["stats"]["number"]["n"] == 1     # errors/blanks never enter stats
    assert col["fill"] == round(1 / 3, 4)


def test_distinct_cap_is_honest():
    acc = ColumnAcc("t")
    for i in range(DISTINCT_CAP + 5):
        acc.add(TEXT, f"v{i}")
    prof = acc.profile()
    assert prof["stats"]["text"]["distinct_capped"] is True
    assert prof["stats"]["text"]["distinct"] is None


def test_empty_column_has_no_stats():
    acc = ColumnAcc("e")
    acc.add("blank", None)
    prof = acc.profile()
    assert prof["dtype"] == "empty"
    assert prof["stats"] is None
