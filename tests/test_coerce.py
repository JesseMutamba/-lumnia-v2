import datetime as dt

import pandas as pd

from app.pipeline.coerce import coerce_number, coerce_date, coerce_value


def test_coerce_number_french_and_percent():
    assert coerce_number("1 234,5") == 1234.5   # NBSP thousands + decimal comma
    assert coerce_number("2 000") == 2000        # regular-space thousands
    assert coerce_number("12%") == 12            # face value, not divided
    assert coerce_number("3.14") == 3.14
    assert coerce_number(10) == 10


def test_coerce_number_declines_on_text():
    assert coerce_number("N/A") is None
    assert coerce_number("BLOC 1") is None


def test_coerce_date():
    assert coerce_date(dt.date(2024, 1, 31)) == "2024-01-31"
    assert coerce_date("31/12/2024") == "2024-12-31"   # dayfirst
    assert coerce_date(pd.Timestamp("2024-01-01")) == "2024-01-01"
    assert coerce_date("not a date") is None


def test_coerce_value_by_kind():
    assert coerce_value("1 234,5") == 1234.5
    assert coerce_value("31/12/2024") == "2024-12-31"
    assert coerce_value("BLOC 1") == "BLOC 1"
    assert coerce_value("#DIV/0!") is None
    assert coerce_value(None) is None
