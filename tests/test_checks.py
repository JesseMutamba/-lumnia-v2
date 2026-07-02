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


def _sheet_with_totals(broken_total: bool = False) -> pd.DataFrame:
    """Data rows, a section subtotal, more data, and a grand total row."""
    grand_ha = 30 + 21 if not broken_total else 60
    return pd.DataFrame([
        ["PARCELLE", "HA", "REGIMES"],
        ["BLOC 1", 10, 100],
        ["BLOC 2", 12, 120],
        ["BLOC 3", 8, 80],
        ["SOUS-TOTAL A", 30, 300],
        ["BLOC 4", 15, 150],
        ["BLOC 5", 6, 60],
        ["SOUS-TOTAL B", 21, 210],
        ["TOTAL GENERAL", grand_ha, 510],
    ])


def test_total_rows_detected_and_reported():
    tidy = orient_sheet(_sheet_with_totals())["tidy"]
    # 1-based sheet rows: subtotals at rows 5 and 8, grand total at row 9.
    assert tidy["total_rows"] == [5, 8, 9]


def test_column_sum_check_verifies_clean_totals():
    findings = orient_sheet(_sheet_with_totals())["tidy"]["checks"]
    sums = [f for f in findings if f["kind"] == "column_sum"]
    assert len(sums) == 3
    assert all(f["status"] == "ok" for f in sums)


def test_column_sum_check_flags_broken_total():
    findings = orient_sheet(_sheet_with_totals(broken_total=True))["tidy"]["checks"]
    bad = [f for f in findings if f["kind"] == "column_sum"
           and f["status"] == "mismatch"]
    assert len(bad) == 1
    m = bad[0]["mismatches"][0]
    assert m["label"] == "HA"                # the offending COLUMN
    assert m["expected"] == 51
    assert m["actual"] == 60
    assert m["row"] == 9


def test_totals_rows_excluded_from_relation_checks():
    """A totals row must not be counted (or flagged) by product/row-sum
    relations: it is a derived row, not an observation."""
    df = pd.DataFrame([
        ["Description", "Qté", "Cout unit", "Montant"],
        ["A", 100, 2.4, 240],
        ["B", 5, 8, 40],
        ["C", 4, 8, 32],
        ["D", 10, 1.5, 15],
        ["E", 3, 45, 135],
        ["F", 200, 0.25, 50],
        # totals row: Qté and Montant are column sums; Qté x Cout unit does
        # NOT hold here and must not be reported as a violation.
        ["TOTAL", 322, None, 512],
    ])
    findings = orient_sheet(df)["tidy"]["checks"]
    prod = next(f for f in findings if f["kind"] == "product")
    assert prod["status"] == "ok"
    assert prod["n_checked"] == 6            # totals row not among them


def test_hierarchical_section_totals_are_unverified_not_false_mismatches():
    """Sections where parent rows and their children BOTH appear make the
    naive rows-above sum double-count; the claimed total must come back
    'unverified', never as a fabricated mismatch."""
    df = pd.DataFrame([
        ["POSTE", "MONTANT", "PART"],
        ["PLANTATIONS", 100, 10],       # parent = sum of next 2 rows
        ["PRODUCTION", 60, 6],
        ["MAINTENANCE", 40, 4],
        ["TECHNIQUES", 50, 5],          # parent = sum of next 2 rows
        ["USINE", 30, 3],
        ["GARAGE", 20, 2],
        ["SOUS-TOTAL", 150, 15],        # correct at group level; naive sum = 300
    ])
    findings = orient_sheet(df)["tidy"]["checks"]
    colsum = [f for f in findings if f["kind"] == "column_sum"]
    assert len(colsum) == 1
    assert colsum[0]["status"] == "unverified"
    assert colsum[0]["mismatches"] == []
    assert colsum[0]["total_abs_delta"] == 0


def test_constant_columns_do_not_fake_totals():
    """Repeated identical values (rates, densities) must not make ordinary
    rows look like totals of the row above."""
    df = pd.DataFrame([
        ["BLOC", "DENSITE", "TAUX", "HA"],
        ["A", 143, 0.15, 800],
        ["B", 143, 0.15, 1200],
        ["C", 143, 0.15, 1100],
        ["D", 143, 0.15, 1021],
    ])
    tidy = orient_sheet(df)["tidy"]
    assert tidy["total_rows"] is None


def test_mismatch_carries_excel_cell_and_fix_confidence():
    findings = _findings(_sheet_qty_unit_total())
    prod = next(f for f in findings if f["kind"] == "product")
    # rule holds on 5/6 rows -> the cell is wrong, suggest with confidence
    assert prod["fix_confidence"] == "high"
    m = prod["mismatches"][0]
    assert m["cell"] == "D3"                 # Montant column, sheet row 3
    assert m["expected"] == 40               # the suggested correction

    clean = _findings(pd.DataFrame([
        ["Description", "Qté", "Cout unit", "Montant"],
        ["A", 2, 3, 6], ["B", 4, 5, 20], ["C", 10, .5, 5],
        ["D", 7, 2, 14], ["E", 3, 9, 27],
    ]))
    ok = next(f for f in clean if f["status"] == "ok")
    assert ok["fix_confidence"] is None      # nothing to fix


def test_cell_addresses_shift_with_panel_offset():
    """A broken table in the RIGHT panel must report absolute sheet cells."""
    left = [
        ["PARCELLE", "HA", "REGIMES"],
        ["BLOC 1", 10, 100],
        ["BLOC 2", 12, 120],
        ["BLOC 3", 8, 80],
        ["BLOC 4", 9, 90],
        ["BLOC 5", 11, 110],
        ["BLOC 6", 7, 70],
    ]
    right = [
        ["Description", "Qté", "Prix", "Montant"],
        ["A", 100, 2.4, 240],
        ["B", 5, 8, 400],                    # should be 40 -> col H (7), row 3
        ["C", 4, 8, 32],
        ["D", 10, 1.5, 15],
        ["E", 3, 45, 135],
        ["F", 200, 0.25, 50],
    ]
    df = pd.DataFrame([l + [None] + r for l, r in zip(left, right)])
    out = orient_sheet(df)
    panel = next(p for p in out["panels"] if p["col_start"] == 4)
    prod = next(f for f in panel["tidy"]["checks"] if f["kind"] == "product")
    assert prod["mismatches"][0]["cell"] == "H3"


def test_cross_examination_vetoes_multi_rule_breakers():
    """A row that violates TWO different rules is structurally different (a
    summary line or a broken input), not a typo — no cell replacement is
    suggested for it, while a clean single-rule typo keeps its suggestion."""
    df = pd.DataFrame([
        ["Description", "Qté", "Prix", "Montant", "Montant2"],
        ["A", 100, 2.4, 240, 240],
        ["B", 5, 8, 400, 400],       # breaks BOTH products -> input suspect
        ["C", 4, 8, 32, 320],        # breaks only Montant2 -> genuine typo
        ["D", 10, 1.5, 15, 15],
        ["E", 3, 45, 135, 135],
        ["F", 200, 0.25, 50, 50],
        ["G", 6, 7, 42, 42],
    ])
    findings = orient_sheet(df)["tidy"]["checks"]
    m1 = next(f for f in findings if f["target"] == "Montant")
    m2 = next(f for f in findings if f["target"] == "Montant2")
    row_b_1 = next(m for m in m1["mismatches"] if m["label"] == "B")
    row_b_2 = next(m for m in m2["mismatches"] if m["label"] == "B")
    row_c = next(m for m in m2["mismatches"] if m["label"] == "C")
    assert row_b_1["suggest"] is False       # vetoed: breaks 2 rules
    assert row_b_2["suggest"] is False
    assert row_c["suggest"] is True          # single-rule typo: suggest fix
    assert m1["fix_confidence"] == "review"  # its only mismatch was vetoed
    assert m2["fix_confidence"] == "review"  # one of two vetoed -> review


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
