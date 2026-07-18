"""Cross-sheet ratio metrics (watch list #2, now built).

Numerator and denominator living on DIFFERENT sheets of one workbook join
onto the spine axis by exact period-string equality — the spine axis never
grows or reorders. Fewer than 3 aligned cells on both sides is an honest
gap (``model["gaps"]``), never a metric. The larger volume series is the
raw input (FFB), the smaller the output (CPO), so volume_ratio stays an
extraction rate below 1 — same convention as the same-sheet path.
"""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MONTHS = [dt.date(2025, m, 28) for m in range(7, 13)]
FFB = [50, 160, 250, 400, 320, 80]
CPO = [10, 40, 50, 100, 80, 16]


def _book(*sheets: tuple) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets:
            df.to_excel(xw, sheet_name=name, header=False, index=False)
    return buf.getvalue()


def _analyze(content: bytes, name="pvak.xlsx") -> dict:
    return client.post("/analyze", files={
        "file": (name, content, "application/octet-stream")}).json()


def _recolte(months=MONTHS, ffb=FFB) -> pd.DataFrame:
    return pd.DataFrame([
        ["RAPPORT RECOLTE", *([None] * len(months))],
        ["SERIE", *months],
        ["RECOLTE FFB (T)", *ffb],
        # aggregate row that ALSO matches the volume vocabulary and out-sums
        # the clean series — must still never carry the role
        ["TOTAL PRODUCTION", *[v * 2 for v in ffb]],
    ])


def _usine(months=MONTHS, cpo=CPO) -> pd.DataFrame:
    return pd.DataFrame([
        ["PRODUCTION USINE", *([None] * len(months))],
        ["SERIE", *months],
        ["PRODUCTION CPO (T)", *cpo],
    ])


def _year(rows, years=(2025, 2026, 2027, 2028)) -> pd.DataFrame:
    return pd.DataFrame([[None, *years], *rows])


def test_monthly_extraction_rate_joins_two_sheets():
    rep = _analyze(_book(("RECOLTE", _recolte()), ("USINE", _usine())))
    mo = rep["model"]["monthly"]
    met = mo["metrics"]
    assert met["volume"]["label"] == "RECOLTE FFB (T)"
    assert met["volume"]["sheet"] == "RECOLTE"
    assert met["volume_secondary"]["label"] == "PRODUCTION CPO (T)"
    assert met["volume_secondary"]["sheet"] == "USINE"
    assert mo["derived"]["volume_ratio"] == [0.2, 0.25, 0.2, 0.25, 0.25, 0.2]
    labels = {s["label"] for s in met.values()}
    assert not any("TOTAL" in l for l in labels)
    assert not rep["model"].get("gaps")


def test_yearly_ratio_swaps_input_to_primary_across_sheets():
    """The spine sheet holds the OUTPUT (CPO); the input (FFB) arrives from
    another sheet and is larger, so it takes ``volume`` and the spine's own
    series steps down to ``volume_secondary`` — ratio stays output/input."""
    recap = _year([["PRODUCTION CPO", 10, 20, 40, 80],
                   ["EFFECTIFS", 5, 6, 7, 8]])
    recolte = _year([["RECOLTE FFB (tonnes)", 50, 100, 160, 200],
                     ["SUPERFICIE HA", 5, 5, 5, 5]])
    m = _analyze(_book(("RECAP", recap), ("RECOLTE", recolte)))["model"]
    met = m["metrics"]
    assert met["volume"]["label"] == "RECOLTE FFB (tonnes)"
    assert met["volume"]["sheet"] == "RECOLTE"
    assert met["volume_secondary"]["label"] == "PRODUCTION CPO"
    assert met["volume_secondary"]["sheet"] == "RECAP"
    assert m["source_sheet"] == "RECAP"          # spine unchanged by the swap
    assert m["derived"]["volume_ratio"] == [0.2, 0.2, 0.25, 0.4]


def test_partial_overlap_joins_on_shared_periods_only():
    """2024-2027 FFB against a 2025-2028 spine: three shared years clear the
    gate; the spine axis is authoritative, 2028 honestly holds None."""
    recap = _year([["PRODUCTION CPO", 10, 20, 40, 80],
                   ["EFFECTIFS", 5, 6, 7, 8]])
    recolte = _year([["RECOLTE FFB (tonnes)", 40, 50, 100, 160]],
                    years=(2024, 2025, 2026, 2027))
    m = _analyze(_book(("RECAP", recap), ("RECOLTE", recolte)))["model"]
    assert m["periods"] == ["2025", "2026", "2027", "2028"]
    assert m["metrics"]["volume"]["values"] == [50, 100, 160, None]
    assert m["derived"]["volume_ratio"] == [0.2, 0.2, 0.25, None]
    assert not m.get("gaps")


def test_opex_per_tonne_across_sheets_matches_same_sheet_arithmetic():
    """Same planted numbers as test_model's same-sheet oracle: a break here
    with test_model green isolates the cross-sheet join as the culprit."""
    recap = _year([["REVENUS BRUTS", 100, 200, 400, 800],
                   ["DEPENSES OPEX", 60, 90, 150, 240]])
    prod = _year([["PRODUCTION CPO", 10, 20, 40, 80]])
    m = _analyze(_book(("RECAP", recap), ("PROD ANNUELLE", prod)))["model"]
    assert m["metrics"]["volume"]["sheet"] == "PROD ANNUELLE"
    assert m["derived"]["opex_per_volume"] == [6, 4.5, 3.75, 3]


def test_misaligned_axes_gap_honestly_instead_of_guessing():
    """A 2024 mill sheet against a 2025 harvest sheet shares zero periods:
    no ratio, no guess — one honest gap entry, and nothing else changes."""
    rep = _analyze(_book(
        ("RECOLTE", _recolte()),
        ("USINE", _usine(months=[dt.date(2024, m, 28) for m in (1, 2, 3, 4)],
                         cpo=[10, 40, 50, 100]))))
    mo = rep["model"]["monthly"]
    assert "volume_secondary" not in mo["metrics"]
    assert "volume_ratio" not in mo["derived"]
    gaps = rep["model"]["gaps"]
    assert [g["metric"] for g in gaps] == ["volume_ratio"]
    assert gaps[0]["requires"]


def test_duplicate_period_in_foreign_axis_is_excluded_not_guessed():
    """A repeated month in the foreign header is unknowable — which cell is
    truth? Those periods join as None; they never corrupt the swap or the
    ratio (the bug rendered 500% extraction rates from last-wins joins)."""
    months_dup = [MONTHS[0], MONTHS[1], MONTHS[1], MONTHS[3], MONTHS[4],
                  MONTHS[5]]
    rep = _analyze(_book(
        ("RECOLTE", _recolte()),
        ("USINE", _usine(months=months_dup, cpo=[10, 40, 999, 100, 80, 16]))))
    mo = rep["model"]["monthly"]
    assert mo["metrics"]["volume"]["label"] == "RECOLTE FFB (T)"   # no flip
    assert mo["derived"]["volume_ratio"] == \
        [0.2, None, None, 0.25, 0.25, 0.2]


def test_yearly_pair_does_not_wipe_monthly_gap():
    """Gap lists are per grain: a yearly sheet holding its own FFB+CPO pair
    must not delete the monthly misalignment gap (the wipe is only sound
    within the grain whose pair actually completed)."""
    recap = _year([["RECOLTE FFB (tonnes)", 50, 100, 160, 200],
                   ["PRODUCTION CPO", 10, 20, 40, 80]])
    rep = _analyze(_book(
        ("RECOLTE", _recolte()),
        ("USINE", _usine(months=[dt.date(2024, m, 28) for m in (1, 2, 3, 4)],
                         cpo=[10, 40, 50, 100])),
        ("RECAP", recap)))
    gaps = rep["model"].get("gaps") or []
    assert [(g["metric"], g["grain"]) for g in gaps] == \
        [("volume_ratio", "monthly")]
    # and the yearly same-sheet pair still derives its own ratio
    assert rep["model"]["derived"]["volume_ratio"] == [0.2, 0.2, 0.25, 0.4]


def test_foreign_sheet_carrying_its_own_pair_keeps_the_ratio():
    """Regression lock: a sheet whose own FFB+CPO pair supplements a spine
    without volume must deliver BOTH series — the old engine did."""
    recap = _year([["REVENUS BRUTS", 100, 200, 400, 800],
                   ["DEPENSES OPEX", 60, 90, 150, 240]])
    prod = _year([["RECOLTE FFB (tonnes)", 50, 100, 160, 200],
                  ["PRODUCTION CPO", 10, 20, 40, 80]])
    m = _analyze(_book(("RECAP", recap), ("PROD", prod)))["model"]
    assert m["metrics"]["volume"]["sheet"] == "PROD"
    assert m["metrics"]["volume_secondary"]["sheet"] == "PROD"
    assert m["derived"]["volume_ratio"] == [0.2, 0.2, 0.25, 0.4]
    assert m["derived"]["opex_per_volume_out"] == [6, 4.5, 3.75, 3]
    assert not m.get("gaps")


def test_sparse_series_on_identical_axis_still_admitted():
    """Regression lock: the >=3 overlap gate exists for cross-axis joins;
    an IDENTICAL axis keeps the old unconditional admission, however sparse
    (dropping it silently deleted capex/net_cash on rerun)."""
    recap = _year([["REVENUS BRUTS", 100, 200, 400, 800],
                   ["DEPENSES OPEX", 60, 90, 150, 240]])
    capex = _year([["INVESTISSEMENTS", 500, 300, None, None]])
    m = _analyze(_book(("RECAP", recap), ("CAPEX", capex)))["model"]
    assert m["metrics"]["capex"]["values"] == [500, 300, None, None]
    assert m["derived"]["net_cash"] == [-460, -190, None, None]


def test_cash_split_counts_only_the_common_window():
    """Cross-sheet monthly opex/capex may cover different windows; the
    invested share is only honest over periods where BOTH are tracked."""
    journal = pd.DataFrame([
        ["JOURNAL SITE", *([None] * 6)],
        ["SERIE", *MONTHS],
        ["CHARGES OPEX", 10, 10, 10, 10, 10, 10],
        ["PRODUCTION CPO (T)", *CPO],
    ])
    capex = pd.DataFrame([
        ["INVESTISSEMENTS", *([None] * 3)],
        ["SERIE", *MONTHS[:3]],
        ["CAPEX PLANTATION", 30, 30, 30],
    ])
    # _cash_split only runs alongside a year spine (existing behavior)
    recap = _year([["REVENUS BRUTS", 100, 200, 400, 800]])
    rep = _analyze(_book(("JOURNAL", journal), ("CAPEX", capex),
                         ("RECAP", recap)))
    cash = rep["model"]["monthly"]["cash"]
    assert cash == {"opex_total": 30, "capex_total": 90,
                    "total": 120, "invested_pct": 75.0}


def test_partial_overlap_marks_dropped_out_of_window_cells():
    """A foreign 2024 cell outside the 2025-2028 spine is excluded from the
    join — and the exclusion is declared, so no total quietly understates
    what the workbook holds."""
    recap = _year([["PRODUCTION CPO", 10, 20, 40, 80],
                   ["EFFECTIFS", 5, 6, 7, 8]])
    recolte = _year([["RECOLTE FFB (tonnes)", 40, 50, 100, 160]],
                    years=(2024, 2025, 2026, 2027))
    m = _analyze(_book(("RECAP", recap), ("RECOLTE", recolte)))["model"]
    assert m["metrics"]["volume"]["dropped_outside_axis"] == 1
    assert "dropped_outside_axis" not in m["metrics"]["volume_secondary"]
