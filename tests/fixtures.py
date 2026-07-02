"""Small synthetic sheets, one per orientation.

Deliberately NOT the plantation file: heuristics must be proven on generic
shapes, never fitted to one workbook.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

D1, D2, D3 = dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 3)


def tidy_sheet() -> pd.DataFrame:
    """Title banner, then a header row, then records. Column 0 has a merged
    label (blank on the second row) that must be forward-filled."""
    return pd.DataFrame([
        ["Rapport statistique", None, None, None],
        ["PARCELLE", "ANNEE", "HA", "REGIMES"],
        ["BLOC 2019", 2019, 10, 100],
        [None, 2019, 12, 120],
        ["BLOC 2020", 2020, 8, 80],
    ])


def matrix_sheet() -> pd.DataFrame:
    """Metric labels down column 0, dates running across the columns."""
    return pd.DataFrame([
        ["PRODUCTION USINE", None, None, None],
        ["METRIC", D1, D2, D3],
        ["Regimes", 10, 20, 30],
        ["Tonnage", 5, 6, 7],
    ])


def year_matrix_sheet() -> pd.DataFrame:
    """RECAP-style cross-tab: metric labels down column 0, *year numbers*
    (plus a year-range label and a grand-total column) across the top."""
    return pd.DataFrame([
        ["PROJECTIONS EXPLOITATION", None, None, None, None, None],
        [None, "2019-2021", 2025, 2026, 2027, "GT"],
        ["HECTARES", 475, 304, 800, 1200, 2779],
        ["PRODUCTION", None, 1186.8, 3353, 4704, 9243.8],
        ["REVENUES", None, 238939, 771190, 1081920, 2092049],
    ])


def form_sheet() -> pd.DataFrame:
    """Sparse key/value report."""
    return pd.DataFrame([
        ["Fiche des operations", None],
        ["Date", "01/01/2024"],
        ["Operateur", "Jean"],
        ["Bloc", "B12"],
        ["Note", "ras"],
    ])


def multi_header_sheet() -> pd.DataFrame:
    """Two stacked header rows with a horizontally-merged group label in the
    top row (only the first cell of each group is populated)."""
    return pd.DataFrame([
        ["Bloc", None, "Production", None],   # group row (merged cells)
        ["ID", "Name", "Jan", "Feb"],          # sub-header row
        ["1", "A", 10, 20],
        ["2", "B", 30, 40],
    ])


def complementary_header_sheet() -> pd.DataFrame:
    """Two header rows that fill each other's gaps: the first carries the
    period labels on the right, the second carries the field names on the
    left. Common in budget/capex sheets."""
    return pd.DataFrame([
        ["BUDGET 2026", None, None, "Jan", "Fev", "Mar"],
        ["Description", "Unité", "Qté", None, None, None],
        ["Semences", "nbre", 100, 10, 20, 30],
        ["Machettes", "pièce", 5, 1, 2, 2],
    ])


def blank_header_cells_sheet() -> pd.DataFrame:
    """Header row with a blank cell: the column must be named col_N, never
    the string 'nan'."""
    return pd.DataFrame([
        ["PARCELLE", None, "HA"],
        ["BLOC 1", "a", 10],
        ["BLOC 2", "b", 8],
    ])


def french_numbers_sheet() -> pd.DataFrame:
    """Tidy table whose numeric column uses French formatting + a % cell."""
    return pd.DataFrame([
        ["PARCELLE", "RENDEMENT", "TAUX"],
        ["BLOC 1", "1 234,5", "12%"],
        ["BLOC 2", "2 000", "8%"],
    ])


def multiband_header_sheet() -> pd.DataFrame:
    """Sparse multi-band header: banner, field names, year band and a MONTANT
    sub-label spread over several rows (with blanks and a one-cell section
    banner in between) — no single row is a valid header on its own.
    Years label column *pairs* (qty, amount) as in real budget sheets — so
    this is banded, not a year-axis matrix."""
    return pd.DataFrame([
        ["ITEM", "DEPENSES", None, None, None, None, None, None],
        [None, "Description", "Unité", "Qté", None, None, None, None],
        [None, None, None, None, None, None, None, None],
        [None, None, None, None, 2026, 2026, 2027, 2027],
        [None, "SECTION A", None, None, None, None, None, None],
        [1, "Machette", "Pièce", 8, 50, 400, 50, 400],
        [2, "Pelle", "Pièce", 4, 10, 80, 10, 80],
        [3, "Lime", "Pièce", 10, 2, 20, 2, 20],
    ])


def side_by_side_sheet() -> pd.DataFrame:
    """Two independent tidy tables on one sheet, separated by a blank column."""
    return pd.DataFrame([
        ["PARCELLE", "HA", "REGIMES", None, "POSTE", "BUDGET", "REEL"],
        ["BLOC 1", 10, 100, None, "Salaires", 60, 58],
        ["BLOC 2", 12, 120, None, "Carburant", 15, 19],
        ["BLOC 3", 8, 80, None, "Entretien", 9, 9],
    ])


def unknown_sheet() -> pd.DataFrame:
    """A raw numeric block: no header, no dates, not sparse -> decline."""
    return pd.DataFrame([
        [1, 2, 3, 4],
        [5, 6, 7, 8],
        [9, 10, 11, 12],
    ])


def empty_sheet() -> pd.DataFrame:
    return pd.DataFrame([[None, None], [None, None]])
