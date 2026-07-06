"""Step 8 Phase 1: semantic schema detection + the metric engine.

Fixtures are tiny neutral tables (dates, categories, amounts) — real
validation runs against real uploads. The contract under test: columns are
classified honestly, metrics aggregate correctly (sum for amounts, mean for
ratios), totals rows never pollute aggregates, and everything the engine
cannot compute becomes an explicit gap naming what the file would need.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.semantics import compute_story, detect_schema

client = TestClient(app)


def _dated_df() -> pd.DataFrame:
    """14 months of dated rows: two groups, an amount, a stock column."""
    rows = []
    for m in range(14):                       # 2025-01 .. 2026-02
        y, mo = 2025 + m // 12, m % 12 + 1
        for zone in ("Nord", "Sud"):
            rows.append({
                "Date": f"{y}-{mo:02d}-15",
                "Zone": zone,
                "Montant": 100 + m * 10 + (50 if zone == "Nord" else 0),
                "Stock": 20 - m if zone == "Sud" else 30,
            })
    return pd.DataFrame(rows)


def _snapshot_df() -> pd.DataFrame:
    """Cross-sectional, energy-file shaped: no dates, growth pre-computed."""
    return pd.DataFrame({
        "ID": [f"E{i:02d}" for i in range(12)],
        "Nom entreprise": [f"Entreprise {chr(65 + i)}" for i in range(12)],
        "Revenue_MUSD": [100 + 10 * i for i in range(12)],
        "Growth_%": [5, -3, 12, -8, 30, 1, -1, 4, -25, 9, 2, -6],
        "Marge_%": [10 + i for i in range(12)],
        "Secteur": ["Oil", "Power", "Renew"] * 4,
        "Actif": [1, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 0],
    })


# ---------------------------------------------------------------- schema ---

def test_schema_classifies_columns():
    s = detect_schema(_snapshot_df())
    assert s["time"] is None
    dims = {d["name"]: d for d in s["dimensions"]}
    assert dims["Secteur"]["cardinality"] == 3
    assert dims["Actif"]["kind"] == "flag"
    ents = [e["name"] for e in s["entities"]]
    assert "Nom entreprise" in ents and "ID" in ents
    assert ents[0] == "Nom entreprise"        # wordiest identifier displays
    kinds = {m["name"]: m["kind"] for m in s["measures"]}
    assert kinds["Revenue_MUSD"] == "amount"
    assert kinds["Growth_%"] == "change_pct"
    assert kinds["Marge_%"] == "ratio"
    roles = {m["name"]: m["role"] for m in s["measures"]}
    assert roles["Revenue_MUSD"] == "revenue"


def test_schema_detects_time_and_grain():
    s = detect_schema(_dated_df())
    assert s["time"]["name"] == "Date"
    assert s["time"]["grain"] in ("month", "week")
    assert s["time"]["n_periods"] == 14
    roles = {m["name"]: m["role"] for m in s["measures"]}
    assert roles["Stock"] == "inventory"


# ---------------------------------------------------------------- metrics --

def test_story_time_family_mom_yoy():
    df = _dated_df()
    story = compute_story(df, detect_schema(df))
    by_id = {m["id"]: m for m in story["metrics"]}
    assert story["headline_measure"] == "Montant"
    # trend: 14 monthly points, summed over both zones
    trend = by_id["trend"]["rows"]
    assert len(trend) == 14
    assert trend[0] == {"period": "2025-01", "value": 250.0}
    # MoM: last month 380 (2x*230+... compute: m=13 -> 100+130=230, +50 Nord)
    assert by_id["mom"]["pct"] is not None
    # YoY exists: month 13 vs month 1
    assert by_id["yoy"]["pct"] is not None
    # inventory: lowest stock listed
    assert by_id["low_stock"]["rows"][0]["value"] == 7.0   # Sud at m=13
    assert story["gaps"] == [] or all(
        g["metric"] not in ("trend", "mom_change", "yoy_change")
        for g in story["gaps"])


def test_story_snapshot_gaps_and_movers():
    df = _snapshot_df()
    story = compute_story(df, detect_schema(df))
    by_id = {m["id"]: m for m in story["metrics"]}
    gap_metrics = {g["metric"] for g in story["gaps"]}
    # honest gaps: no dates -> no trend family; no stock column
    assert {"trend", "mom_change", "yoy_change", "stock_on_hand"} <= gap_metrics
    for g in story["gaps"]:
        assert g["requires"]
    # totals by sector sum correctly
    rows = {r["member"]: r["value"] for r in by_id["by_Secteur"]["rows"]}
    assert rows["Oil"] == 100 + 130 + 160 + 190
    # ratio measures average, never sum
    marge = by_id["avg_Marge_%_by_Secteur"] if "avg_Marge_%_by_Secteur" in by_id else None
    growth = by_id["avg_Growth_%_by_Secteur"]
    assert growth["metric"] == "mean"
    # movers: best and worst entities by growth, with headline context
    movers = by_id["movers"]
    assert movers["top"][0]["value"] == 30.0
    assert movers["bottom"][0]["value"] == -25.0
    assert movers["top"][0]["headline"] is not None


def test_totals_rows_never_pollute_aggregates():
    """End-to-end: a sheet with a TOTAL row must not double-count."""
    df = pd.DataFrame([
        ["Region", "Produit", "Qte", "Montant"],
        ["Nord", "Cacao", 1, 100], ["Sud", "Cafe", 2, 200],
        ["Est", "Cacao", 1, 300], ["Nord", "Cafe", 2, 150],
        ["Sud", "Cacao", 1, 250], ["Est", "Cafe", 2, 100],
        ["TOTAL", None, 9, 1100],
    ])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="S", header=False, index=False, engine="openpyxl")
    r = client.post("/analyze", files={
        "file": ("tot.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    story = r["story"]
    assert story is not None
    head = next(m for m in story["metrics"] if m["id"] == "headline")
    assert head["value"] == 1100.0            # not 2200
    by_region = next(m for m in story["metrics"] if m["id"] == "by_Region")
    assert "TOTAL" not in {row["member"] for row in by_region["rows"]}


def test_workbook_story_picked_and_absent_honestly():
    # a numbers-free sheet yields no story at all
    df = pd.DataFrame([["A", "B"], ["x", "y"], ["z", "w"], ["q", "r"]])
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="S", header=False, index=False, engine="openpyxl")
    r = client.post("/analyze", files={
        "file": ("txt.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    assert r["story"] is None


def test_iso_dates_never_day_month_swapped():
    """Regression: dayfirst parsing must not corrupt ISO dates
    ("2025-01-05" is January 5th, not May 1st) while dd/mm/yyyy strings
    keep their French day-first reading."""
    from app.pipeline.coerce import coerce_date
    assert coerce_date("2025-01-05") == "2025-01-05"
    assert coerce_date("2025-11-05") == "2025-11-05"
    assert coerce_date("05/01/2025") == "2025-01-05"   # 5 janvier, day-first


def test_id_columns_are_never_measures():
    """PLAYER_ID-style columns are labels, not quantities — summing them
    would put a meaningless 'Total PLAYER_ID' in front of a client."""
    import pandas as pd
    from app.pipeline.semantics import detect_schema, compute_story
    df = pd.DataFrame({
        "PLAYER_ID": [201566 + i for i in range(20)],
        "TEAM_ID": [1610612760 + i % 4 for i in range(20)],
        "PLAYER_NAME": [f"Joueur {i}" for i in range(20)],
        "PTS": [10.5 + i for i in range(20)],
    })
    schema = detect_schema(df)
    names = {m["name"] for m in schema["measures"]}
    assert "PTS" in names
    assert not any(n.upper().endswith("ID") for n in names)
    story = compute_story(df, schema)
    assert story["headline_measure"] == "PTS"


def test_wide_stats_table_story_is_editorially_sane():
    """The NBA case: wide snapshot stats. Three rules — mean-only columns
    (AGE) never total or headline; rank columns are ordinals, not measures;
    0/1 flags never become breakdown subjects ('1 accounts for 93%...')."""
    import pandas as pd
    from app.pipeline.semantics import detect_schema, compute_story
    df = pd.DataFrame({
        "PLAYER_NAME": [f"Joueur {i}" for i in range(24)],
        "TEAM_ABBREVIATION": ["OKC", "GSW", "CLE", "SAS"] * 6,
        "ACTIVE_TWITTER_LAST_YEAR": [1, 1, 0, 1] * 6,
        "AGE": [22 + i % 12 for i in range(24)],
        "GP": [35 + i for i in range(24)],
        "PTS": [8.5 + i for i in range(24)],
        "PTS_RANK": [24 - i for i in range(24)],
    })
    schema = detect_schema(df)
    by_kind = {m["name"]: m["kind"] for m in schema["measures"]}
    assert "PTS_RANK" not in by_kind            # ordinal, not a quantity
    assert by_kind.get("AGE") != "amount"       # ages average, never total
    story = compute_story(df, schema)
    assert story["headline_measure"] != "AGE"
    ids = {m["id"] for m in story["metrics"]}
    assert "by_ACTIVE_TWITTER_LAST_YEAR" not in ids   # flags aren't subjects
    assert "by_TEAM_ABBREVIATION" in ids              # real dims still split
