from app.pipeline.orient import classify, orient_sheet
from tests.fixtures import (
    tidy_sheet, matrix_sheet, form_sheet, unknown_sheet, empty_sheet,
    multi_header_sheet, french_numbers_sheet,
    complementary_header_sheet, blank_header_cells_sheet, year_matrix_sheet,
    multiband_header_sheet, side_by_side_sheet, stacked_tables_sheet,
)


def test_stacked_tables_split_into_bands():
    """A sheet that declines as a whole splits on blank-row runs; each band
    classifies on its own with row references still valid for the real sheet."""
    out = orient_sheet(stacked_tables_sheet())
    assert out["orientation"] == "multi"
    assert "stacked" in out["reason"]
    bands = out["panels"]
    assert len(bands) == 2
    top, bottom = bands
    assert (top["row_start"], top["row_end"]) == (0, 4)
    assert top["orientation"] == "tidy"
    assert top["tidy"]["columns"][:2] == ["ANNEE", "HA"]
    # header_rows point at the REAL sheet rows, not the band slice
    assert top["tidy"]["header_rows"] == [1]
    assert bottom["row_start"] == 17
    assert bottom["orientation"] == "matrix"
    assert bottom["tidy"]["summary"]["n_series"] == 2


def test_recognizable_sheets_are_never_banded():
    """The band fallback must not shred sheets that already classify —
    including ones with internal blank spacer rows (multiband headers)."""
    for fixture in (tidy_sheet, matrix_sheet, multiband_header_sheet,
                    form_sheet, year_matrix_sheet):
        out = orient_sheet(fixture())
        assert out["orientation"] != "multi", fixture.__name__


def test_multiband_composite_header():
    """Banner + field names + year band, none valid alone, merge into one
    header; the one-cell section banner is not part of it."""
    out = orient_sheet(multiband_header_sheet())
    assert out["orientation"] == "tidy"
    tidy = out["tidy"]
    assert tidy["header_rows"] == [0, 1, 3]
    cols = tidy["columns"]
    assert "ITEM" in cols[0]
    assert any("Description" in c for c in cols)
    assert any("2027" in c for c in cols)
    # data rows follow, including the sparse SECTION A row
    recs = tidy["records"]
    machette = next(r for r in recs if any(v == "Machette" for v in r.values()))
    year_cols = [c for c in cols if "2027" in c]
    assert len(year_cols) == 2                       # qty + amount pair
    assert [machette[c] for c in year_cols] == [50, 400]


def test_side_by_side_tables_split_into_panels():
    out = orient_sheet(side_by_side_sheet())
    assert out["orientation"] == "multi"
    assert len(out["panels"]) == 2
    left, right = out["panels"]
    assert (left["col_start"], left["col_end"]) == (0, 2)
    assert (right["col_start"], right["col_end"]) == (4, 6)
    assert left["orientation"] == "tidy"
    assert right["orientation"] == "tidy"
    assert left["tidy"]["columns"] == ["PARCELLE", "HA", "REGIMES"]
    assert right["tidy"]["columns"] == ["POSTE", "BUDGET", "REEL"]
    assert right["tidy"]["records"][0]["POSTE"] == "Salaires"
    assert out["tidy"] is None


def test_single_table_sheets_do_not_split():
    for fixture in (tidy_sheet, matrix_sheet, year_matrix_sheet):
        out = orient_sheet(fixture())
        assert out["orientation"] != "multi", fixture.__name__
        assert out["panels"] is None


def test_classify_tidy():
    r = classify(tidy_sheet())
    assert r["orientation"] == "tidy"
    assert r["confidence"] > 0


def test_classify_matrix():
    r = classify(matrix_sheet())
    assert r["orientation"] == "matrix"
    assert r["confidence"] > 0


def test_classify_year_matrix():
    r = classify(year_matrix_sheet())
    assert r["orientation"] == "matrix"
    assert r["meta"]["axis"] == "year"
    # The grand-total column (text 'GT' header) is NOT a period.
    assert len(r["meta"]["period_cols"]) == 4


def test_year_matrix_unpivots_with_year_periods():
    tidy = orient_sheet(year_matrix_sheet())["tidy"]
    assert tidy["columns"][-2:] == ["period", "value"]
    # HECTARES has 4 period cells, PRODUCTION/REVENUES have 3 each.
    assert tidy["n_records"] == 10
    first = tidy["records"][0]
    assert first["period"] == "2019-2021"    # range label kept verbatim
    assert first["value"] == 475
    assert tidy["records"][1]["period"] == 2025

    s = tidy["summary"]
    assert s["n_series"] == 3
    assert s["n_periods"] == 4
    assert s["period_min"] == 2019
    assert s["period_max"] == 2027


def test_data_rows_with_one_year_are_not_an_axis():
    """A tidy table whose records merely contain a year value must stay tidy."""
    r = classify(tidy_sheet())                # rows carry ANNEE=2019/2020 cells
    assert r["orientation"] == "tidy"


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


def test_complementary_header_rows_merge():
    """Row 0 carries the period labels, row 1 the field names — together they
    are one header, even though row 1 is sparse relative to the data rows."""
    tidy = orient_sheet(complementary_header_sheet())["tidy"]
    assert tidy["header_rows"] == [0, 1]
    assert tidy["n_records"] == 2
    cols = tidy["columns"]
    # Field names from the second layer are present, months from the first.
    assert any("Unité" in c for c in cols)
    assert any("Qté" in c for c in cols)
    assert "Jan" in cols and "Mar" in cols
    assert tidy["records"][0]["Jan"] == 10


def test_blank_header_cells_get_positional_names():
    tidy = orient_sheet(blank_header_cells_sheet())["tidy"]
    assert "nan" not in tidy["columns"]
    assert tidy["columns"] == ["PARCELLE", "col_1", "HA"]


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
