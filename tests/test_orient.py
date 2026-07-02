from app.pipeline.orient import classify, orient_sheet
from tests.fixtures import (
    tidy_sheet, matrix_sheet, form_sheet, unknown_sheet, empty_sheet,
    multi_header_sheet, french_numbers_sheet,
)


def test_classify_tidy():
    r = classify(tidy_sheet())
    assert r["orientation"] == "tidy"
    assert r["confidence"] > 0


def test_classify_matrix():
    r = classify(matrix_sheet())
    assert r["orientation"] == "matrix"
    assert r["confidence"] > 0


def test_classify_form():
    r = classify(form_sheet())
    assert r["orientation"] == "form"


def test_classify_unknown():
    assert classify(unknown_sheet())["orientation"] == "unknown"


def test_classify_empty():
    assert classify(empty_sheet())["orientation"] == "unknown"


def test_tidy_extraction_forward_fills_merged_labels():
    out = orient_sheet(tidy_sheet())
    tidy = out["tidy"]
    assert tidy["columns"] == ["PARCELLE", "ANNEE", "HA", "REGIMES"]
    assert tidy["n_records"] == 3
    # Row with a blank (merged) PARCELLE cell inherits the block label above it.
    assert tidy["records"][1]["PARCELLE"] == "BLOC 2019"
    assert tidy["records"][1]["ANNEE"] == 2019


def test_matrix_extraction_unpivots_dates():
    out = orient_sheet(matrix_sheet())
    tidy = out["tidy"]
    assert tidy["columns"] == ["METRIC", "period", "value"]
    # 2 metric rows x 3 date columns = 6 observations
    assert tidy["n_records"] == 6
    first = tidy["records"][0]
    assert first["METRIC"] == "Regimes"
    assert first["period"] == "2024-01-01"
    assert first["value"] == 10


def test_form_and_unknown_have_no_tidy_table():
    assert orient_sheet(form_sheet())["tidy"] is None
    assert orient_sheet(unknown_sheet())["tidy"] is None


def test_multi_row_header_merges_group_and_sub_labels():
    tidy = orient_sheet(multi_header_sheet())["tidy"]
    assert tidy["header_rows"] == [0, 1]
    # Group label (top, horizontally merged) joined with the sub-header.
    assert tidy["columns"] == ["Bloc / ID", "Bloc / Name",
                               "Production / Jan", "Production / Feb"]
    assert tidy["n_records"] == 2


def test_tidy_coerces_french_numbers_and_profiles_columns():
    tidy = orient_sheet(french_numbers_sheet())["tidy"]
    # Values are canonicalised into real numbers downstream metrics can use.
    assert tidy["records"][0]["RENDEMENT"] == 1234.5
    assert tidy["records"][1]["RENDEMENT"] == 2000
    assert tidy["records"][0]["TAUX"] == 12

    types = {c["name"]: c["dtype"] for c in tidy["column_types"]}
    assert types["PARCELLE"] == "text"
    assert types["RENDEMENT"] == "number"
    assert types["TAUX"] == "number"


def test_matrix_column_types():
    tidy = orient_sheet(matrix_sheet())["tidy"]
    types = {c["name"]: c["dtype"] for c in tidy["column_types"]}
    assert types["period"] == "date"
    assert types["value"] == "number"
