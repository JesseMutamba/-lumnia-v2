# Lumnia v2 — backend

A clean-slate, backend-only pipeline for analysing **messy, real-world
spreadsheets** with a **general process — never per-file hardcoding**.

Design principle: **fail honest.** When the pipeline can't determine something it
reports `None` / `"unknown"` rather than guessing wrong.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health`  | liveness |
| `POST` | `/analyze` | upload CSV/Excel (form field `file`) → per-sheet inventory + orientation + tidy extraction |

## Pipeline

```
app/
  main.py              FastAPI app + routes
  models.py            Pydantic response schemas
  pipeline/
    ingest.py          read_upload(content, filename) -> {sheet: raw DataFrame}   (header=None, no cleaning)
    celltypes.py       value-based cell classification: blank / number / date / text
    profile.py         profile_sheet(...) + guess_header_row(...)   (Step 1 header heuristic)
    orient.py          classify(...) orientation + extract tidy/long tables        (Step 2)
    coerce.py          canonicalise values: "1 234,5" -> 1234.5, dates -> ISO
    jsonsafe.py        make raw cell values JSON-serialisable
tests/                 synthetic fixtures per orientation + API smoke tests
```

## What it does

**Step 1 — inventory.** For every sheet: real shape, fill ratio, non-empty
rows/cols, a general header-row heuristic (first well-filled, mostly-text row),
and a small preview.

**Step 2 — orientation + extraction.** Each sheet is classified and, where
possible, extracted into a normalised **tidy long-format** table:

- `tidy` — header row on top, records below. Supports **multi-row headers**
  (stacked header rows merged, horizontally-filled group labels) and
  **forward-filled merged label columns**.
- `matrix` — cross-tab with a date axis across the columns; **unpivoted** to
  `(label…, period, value)`.
- `form` — sparse key/value report.
- `unknown` — declined honestly.

Extracted tables also carry **coerced values** (French numbers, percentages and
dates normalised) and a **per-column type profile** (`number` / `date` / `text`
/ `mixed`) for downstream metrics.

All classification is heuristic and value-based — no sheet names, no
file-specific special cases.

## Run / test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000      # GET /health, POST /analyze (field: file)
pytest -q
```

## Roadmap

1. ✅ Upload → sheet inventory + header heuristic
2. ✅ Orientation detection + tidy-table extraction (+ value coercion, column profiling)
3. Profiling + metrics over the tidy tables
4. Deterministic checks (e.g. qty × unit = total reconciliation) — fail honest
5. Persistence, then an API surface for a future frontend
