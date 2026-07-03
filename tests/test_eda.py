import pytest
from app.pipeline.eda import run_eda, generate_insights


@pytest.fixture
def sample_records():
    return [
        {"price": 100, "quality": 8, "neighborhood": "A", "year": 2000},
        {"price": 200, "quality": 9, "neighborhood": "B", "year": 2001},
        {"price": 150, "quality": None, "neighborhood": "A", "year": 2002},
        {"price": 300, "quality": 7, "neighborhood": "C", "year": 2003},
        {"price": 250, "quality": 10, "neighborhood": "B", "year": 2004},
    ]


def test_run_eda_basic(sample_records):
    result = run_eda(sample_records, sheet_name="test")
    assert result["row_count"] == 5
    assert result["target_column"] == "price"
    assert "quality" in result["missingness"]
    assert result["missingness"]["quality"]["count"] == 1


def test_top_drivers(sample_records):
    result = run_eda(sample_records, sheet_name="test")
    assert len(result["top_drivers"]) >= 1
    assert result["top_drivers"][0]["column"] in ("quality", "year")


def test_generate_insights(sample_records):
    eda = run_eda(sample_records, sheet_name="test")
    insights = generate_insights([eda])
    assert len(insights) > 0
    types = {i["type"] for i in insights}
    assert "missingness" in types or "driver" in types


def test_empty_records():
    result = run_eda([], sheet_name="empty")
    assert result["row_count"] == 0
    assert generate_insights([result]) == []


# --------------------------------------------------------------------------- #
# Integration fixes: full-data path, honesty gates, French targets
# --------------------------------------------------------------------------- #
def test_eda_runs_on_full_columns_not_the_preview_cap():
    """EDA must see every extracted row, not the 8-row record preview."""
    import pandas as pd
    from app.pipeline.orient import orient_sheet

    rows = [["PARCELLE", "MONTANT", "QTE"]]
    for i in range(40):
        rows.append([f"BLOC {i}", 100 + i * 3, 10 + i])
    tidy = orient_sheet(pd.DataFrame(rows))["tidy"]
    assert len(tidy["records"]) == 8            # preview stays capped
    assert tidy["eda"]["row_count"] == 40       # EDA saw everything


def test_french_target_detected():
    import pandas as pd
    from app.pipeline.eda import run_eda_df

    df = pd.DataFrame({"QTE": [1, 2, 3, 4], "MONTANT": [10, 20, 30, 40],
                       "AUTRE": [5, 6, 7, 8]})
    assert run_eda_df(df)["target_column"] == "MONTANT"


def test_no_driver_claims_from_tiny_tables(sample_records):
    """5 rows: facts computed, but no correlation CLAIM is asserted."""
    eda = run_eda(sample_records, sheet_name="tiny")
    assert eda["top_drivers"]                    # facts stay available
    insights = generate_insights([eda])
    assert all(i["type"] not in ("driver", "categorical", "outliers")
               for i in insights)                # claims gated below 10 rows


def test_driver_claims_appear_with_enough_rows():
    recs = [{"montant": i * 2 + (i * 17) % 13, "qte": i, "zone": "AB"[i % 2]}
            for i in range(30)]
    insights = generate_insights([run_eda(recs, sheet_name="big")])
    assert any(i["type"] == "driver" for i in insights)


def test_analyze_response_carries_insights():
    import io
    import pandas as pd
    from fastapi.testclient import TestClient
    from app.main import app

    rows = [["PARCELLE", "QTE", "MONTANT"]]
    for i in range(20):
        rows.append([f"BLOC {i % 4}", i + 1, None if i % 5 == 0 else 100 + i])
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, sheet_name="S", header=False, index=False,
                                engine="openpyxl")
    client = TestClient(app)
    body = client.post("/analyze",
                       files={"file": ("e.xlsx", buf.getvalue(),
                                       "application/octet-stream")}).json()
    assert body["insights"], "workbook-level insights missing"
    assert any(i["type"] == "missingness" for i in body["insights"])
    # persisted too
    stored = client.get(f"/analyses/{body['id']}").json()
    assert stored["insights"] == body["insights"]
