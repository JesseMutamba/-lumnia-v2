from app.pipeline.profile import guess_header_row, profile_sheet
from tests.fixtures import tidy_sheet, matrix_sheet, unknown_sheet


def test_header_skips_title_and_finds_text_row():
    # Row 0 is a one-cell title banner; the real header is row 1.
    assert guess_header_row(tidy_sheet()) == 1


def test_header_declines_on_pure_numeric_block():
    assert guess_header_row(unknown_sheet()) is None


def test_matrix_header_is_not_a_clean_text_row():
    # The date row is mostly dates, so the mostly-text rule should not pick it;
    # there is no clean text header, so None is acceptable/expected here.
    assert guess_header_row(matrix_sheet()) in (None,)


def test_profile_fields():
    prof = profile_sheet("S", tidy_sheet())
    assert prof["name"] == "S"
    assert prof["n_rows"] == 5 and prof["n_cols"] == 4
    assert prof["n_nonempty_rows"] == 5
    assert 0.0 < prof["fill_ratio"] <= 1.0
    assert prof["header_row"] == 1
    # preview is JSON-safe: blanks become None
    assert prof["preview"][0][0] == "Rapport statistique"
    assert prof["preview"][0][1] is None
