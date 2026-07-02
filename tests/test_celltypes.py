import datetime as dt

import numpy as np
import pandas as pd

from app.pipeline.celltypes import cell_kind, is_blank, BLANK, DATE, NUMBER, TEXT


def test_blank_variants():
    for v in [None, "", "   ", float("nan"), pd.NaT, np.nan, "#DIV/0!", "#N/A"]:
        assert is_blank(v), v
        assert cell_kind(v) == BLANK


def test_numbers():
    assert cell_kind(5) == NUMBER
    assert cell_kind(3.14) == NUMBER
    assert cell_kind("42") == NUMBER
    assert cell_kind("1 234,5") == NUMBER   # French thousands + decimal comma
    assert cell_kind("12%") == NUMBER


def test_dates():
    assert cell_kind(dt.date(2024, 1, 1)) == DATE
    assert cell_kind(dt.datetime(2024, 1, 1)) == DATE
    assert cell_kind(pd.Timestamp("2024-01-01")) == DATE
    assert cell_kind("31/12/2024") == DATE
    assert cell_kind("2024-12-31") == DATE


def test_text():
    assert cell_kind("PARCELLE") == TEXT
    assert cell_kind("Bloc 12") == TEXT
    assert cell_kind(True) == TEXT   # bool is a flag, not a metric number
