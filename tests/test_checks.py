"""Step 4: deterministic reconciliation checks (self-auditing tables)."""
from __future__ import annotations

import pandas as pd

from app.pipeline.orient import orient_sheet


def _sheet_qty_unit_total() -> pd.DataFrame:
    """Total = Qty x Unit price, with ONE deliberately broken row (row 4:
    5 x 8 = 40, but 400 was typed — a classic fat-finger error)."""
    return pd.DataFrame([
        ["Description", "Qté", "Cout unit", "Montant"],
        ["Semences", 100, 2.4, 240],
        ["Machettes", 5, 8, 400],       # WRONG: should be 40
        ["Pelles", 4, 8, 32],
        ["Limes", 10, 1.5, 15],
        ["Brouettes", 3, 45, 135],
        ["Sacs", 200, 0.25, 50],
    ])


def _sheet_months_total() -> pd.DataFrame:
    """Annual total = sum of month columns; one row disagrees."""
    return pd.DataFrame([
        ["Poste", "Total", "Jan", "Fev", "Mar"],
        ["Salaires", 60, 10, 20, 30],
        ["Carburant", 15, 5, 5, 5],
        ["Entretien", 99, 3, 3, 3],     # WRONG: should be 9
        ["Divers", 6, 2, 2, 2],
    ])


def _findings(df):
    return orient_sheet(df)["tidy"]["checks"]


def test_product_relation_discovered_and_violation_flagged():
    findings = _findings(_sheet_qty_unit_total())
    prod = next(f for f in findings if f["kind"] == "product")
    assert prod["target"] == "Montant"
    assert set(prod["inputs"]) == {"Qté", "Cout unit"}
    assert prod["n_checked"] == 6
    assert prod["n_mismatched"] == 1
    m = prod["mismatches"][0]
    assert m["label"] == "Machettes"
    assert m["expected"] == 40
    assert m["actual"] == 400
    assert m["delta"] == 360
    assert m["row"] == 3                 # 1-based row in the sheet, like Excel


def test_row_sum_relation_discovered_and_violation_flagged():
    findings = _findings(_sheet_months_total())
    rs = next(f for f in findings if f["kind"] == "row_sum")
    assert rs["target"] == "Total"
    assert rs["inputs"] == ["Jan", "Fev", "Mar"]
    assert rs["n_mismatched"] == 1
    m = rs["mismatches"][0]
    assert m["label"] == "Entretien"
    assert m["expected"] == 9 and m["actual"] == 99


def test_mismatch_findings_sorted_before_ok_findings():
    findings = _findings(_sheet_months_total())
    statuses = [f["status"] for f in findings]
    assert statuses == sorted(statuses, key=lambda s: s == "ok")


def test_clean_table_reports_relation_as_ok():
    df = pd.DataFrame([
        ["Description", "Qté", "Cout unit", "Montant"],
        ["A", 2, 3, 6],
        ["B", 4, 5, 20],
        ["C", 10, 0.5, 5],
        ["D", 7, 2, 14],
        ["E", 3, 9, 27],
    ])
    findings = _findings(df)
    prod = next(f for f in findings if f["kind"] == "product")
    assert prod["status"] == "ok"
    assert prod["n_mismatched"] == 0
    assert prod["total_abs_delta"] == 0


def test_no_relation_no_findings():
    """Unrelated numeric columns must NOT produce fabricated relations."""
    df = pd.DataFrame([
        ["Nom", "A", "B"],
        ["x", 17, 3],
        ["y", 23, 11],
        ["z", 5, 7],
        ["w", 13, 2],
    ])
    assert _findings(df) is None


def test_too_few_rows_declines():
    """Two rows are not evidence of a relation — fail honest."""
    df = pd.DataFrame([
        ["Nom", "Qté", "Prix", "Total"],
        ["x", 2, 3, 6],
        ["y", 4, 5, 20],
    ])
    assert _findings(df) is None


def test_blank_months_count_as_zero_in_row_sums():
    df = pd.DataFrame([
        ["Poste", "Total", "Jan", "Fev", "Mar"],
        ["A", 10, 10, None, None],
        ["B", 7, None, 7, None],
        ["C", 5, None, None, 5],
        ["D", 9, 3, 3, 3],
    ])
    findings = _findings(df)
    rs = next(f for f in findings if f["kind"] == "row_sum")
    assert rs["status"] == "ok"
    assert rs["n_checked"] == 4
