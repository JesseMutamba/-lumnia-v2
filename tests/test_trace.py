"""Oracle: /analyses/{id}/cells re-reads cited cells from the raw upload.

The trace endpoint is the demo's proof step: given the A1 refs a model
figure carries, it must return the values the ORIGINAL file holds at
those cells — re-read from the stored bytes through the same ingest path,
never echoed from the report — plus a small excerpt for visual context.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MONTHS = ["Juil 2025", "Août 2025", "Sept 2025", "Oct 2025",
          "Nov 2025", "Déc 2025"]
CPO = [10, 40, 50, 100, 80, 16]
OPEX = [10, 10, 10, 10, 10, 10]


def _book() -> bytes:
    df = pd.DataFrame([
        ["JOURNAL PRODUCTION", *([None] * 6)],
        ["SERIE", *MONTHS],
        ["PRODUCTION CPO (T)", *CPO],           # row 3, values B3..G3
        ["CHARGES OPEX", *OPEX],                # row 4
        # French-formatted numeric TEXT: raw string, coerced number
        ["FRAIS DIVERS", "1 234,5", *([None] * 5)],   # row 5, B5
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="JOURNAL", header=False, index=False)
    return buf.getvalue()


def _upload() -> dict:
    r = client.post("/analyze", files={
        "file": ("pvak.xlsx", _book(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def test_trace_returns_the_raw_and_coerced_values():
    rep = _upload()
    r = client.get(f"/analyses/{rep['id']}/cells",
                   params={"sheet": "JOURNAL", "refs": "B3,G3,B5"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sheet"] == "JOURNAL"
    by_ref = {c["ref"]: c for c in body["cells"]}
    assert by_ref["B3"]["raw"] == 10 and by_ref["B3"]["value"] == 10
    assert by_ref["G3"]["raw"] == 16 and by_ref["G3"]["value"] == 16
    # the file says "1 234,5"; the pipeline reads 1234.5 — both are shown
    assert by_ref["B5"]["raw"] == "1 234,5"
    assert by_ref["B5"]["value"] == 1234.5


def test_trace_agrees_with_every_model_cell():
    # Round-trip at the API level: each metric value re-derives from the
    # endpoint's own answers for the refs the model emitted.
    rep = _upload()
    met = rep["model"]["monthly"]["metrics"]
    for role, m in met.items():
        for v, refs in zip(m["values"], m["cells"]):
            if v is None:
                continue
            r = client.get(f"/analyses/{rep['id']}/cells",
                           params={"sheet": m["sheet"],
                                   "refs": ",".join(refs)})
            assert r.status_code == 200, r.text
            total = sum(c["value"] for c in r.json()["cells"])
            assert round(total, 4) == v, (role, refs, total, v)


def test_trace_excerpt_shows_the_row_label():
    rep = _upload()
    r = client.get(f"/analyses/{rep['id']}/cells",
                   params={"sheet": "JOURNAL", "refs": "D3"})
    body = r.json()
    ex = body["excerpt"]
    assert ex["col_letters"][0] == "A"
    labels = [row[0] for row in ex["rows"]]
    assert "PRODUCTION CPO (T)" in labels
    # the cited row sits at row_start + offset
    assert ex["row_start"] <= 3 <= ex["row_start"] + len(ex["rows"]) - 1


def test_trace_fails_honest():
    rep = _upload()
    aid = rep["id"]
    assert client.get(f"/analyses/{aid}/cells",
                      params={"sheet": "NOPE", "refs": "B3"}).status_code == 404
    assert client.get(f"/analyses/{aid}/cells",
                      params={"sheet": "JOURNAL",
                              "refs": "3B"}).status_code == 400
    assert client.get(f"/analyses/{aid}/cells",
                      params={"sheet": "JOURNAL", "refs": ""}).status_code == 400
    assert client.get(f"/analyses/{aid}/cells",
                      params={"sheet": "JOURNAL",
                              "refs": "ZZ9999"}).status_code == 400
    assert client.get("/analyses/does-not-exist/cells",
                      params={"sheet": "JOURNAL",
                              "refs": "B3"}).status_code == 404
    too_many = ",".join(f"B{i}" for i in range(1, 43))
    assert client.get(f"/analyses/{aid}/cells",
                      params={"sheet": "JOURNAL",
                              "refs": too_many}).status_code == 400
