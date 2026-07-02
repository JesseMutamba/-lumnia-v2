# Lumnia v2 — backend

A clean-slate, backend-only pipeline for analysing **messy, real-world
spreadsheets** with a **general process — never per-file hardcoding**.

Design principle: **fail honest.** When the pipeline can't determine something it
reports `None` / `"unknown"` rather than guessing wrong.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`    | `/health`                 | liveness |
| `POST`   | `/analyze`                | upload CSV/Excel (form field `file`) → full analysis, persisted with an `id` |
| `GET`    | `/analyses`               | list stored analyses (metadata, newest first) |
| `GET`    | `/analyses/{id}`          | full stored report |
| `GET`    | `/analyses/{id}/findings` | workbook-level audit: every discovered relation, mismatches ranked by money impact |
| `POST`   | `/analyses/{id}/rerun`    | re-run the *current* pipeline on the stored original bytes |
| `DELETE` | `/analyses/{id}`          | remove an analysis and its stored file |

## Pipeline

```
app/
  main.py              FastAPI app + routes
  models.py            Pydantic response schemas
  storage.py           SQLite persistence, stdlib only (report JSON + original bytes)
  findings.py          workbook-level audit aggregation
  pipeline/
    ingest.py          read_upload(content, filename) -> {sheet: raw DataFrame}   (header=None, no cleaning)
    celltypes.py       value-based cell classification: blank / number / date / text
    profile.py         profile_sheet(...) + guess_header_row(...)   (Step 1 header heuristic)
    orient.py          orientation, header merging, panel/band splitting, extraction (Step 2)
    coerce.py          canonicalise values: "1 234,5" -> 1234.5, dates -> ISO
    metrics.py         streaming per-column statistics + table summaries (Step 3)
    checks.py          deterministic reconciliation: product / row-sum relations (Step 4)
    jsonsafe.py        make raw cell values JSON-serialisable
tests/                 synthetic fixtures only — never fitted to one workbook
```

## What it does

**Step 1 — inventory.** For every sheet: real shape, fill ratio, non-empty
rows/cols, a general header-row heuristic (first well-filled, mostly-text row),
and a small preview.

**Step 2 — orientation + extraction.** Each sheet is classified and, where
possible, extracted into a normalised **tidy long-format** table:

- `tidy` — header row on top, records below. Supports **multi-row headers**
  (stacked layers merged, group labels horizontally filled), **complementary
  header pairs** (two rows filling each other's gaps), **sparse multi-band
  headers** (banner / field names / year bands merged, title banners
  stripped), and **forward-filled merged label columns**.
- `matrix` — cross-tab with a **date or year-number axis** across the columns;
  unpivoted to `(label…, period, value)`.
- `form` — sparse key/value report.
- `multi` — several tables on one sheet, **side-by-side** (split on blank
  columns) or **vertically stacked** (split on blank rows, as a fallback when
  the whole sheet declines); each panel is classified and extracted on its own.
- `unknown` — declined honestly.

Extracted tables carry **coerced values** (French numbers, percentages, dates
normalised) and a **per-column type profile**.

**Step 3 — profiling.** Streaming per-column statistics (numeric
min/max/sum/mean/zeros/negatives, date ranges, text top values) and table
summaries, computed in the same pass as extraction. Mixed columns report
per-kind sub-stats; distinct counts cap honestly instead of miscounting.

**Step 4 — reconciliation checks.** Arithmetic relations **discovered from
values, never column names**: products (`Montant = Qté × Cout unit`) and row
sums (`Total = Janvier + … + Décembre`). Every violating row is reported with
expected/actual/delta and its 1-based Excel row. Coincidence guards: minimum
matching rows, 70% support, dedup of algebraic rearrangements, and zero-heavy
matches don't count as evidence.

**Step 5 — persistence.** Stdlib SQLite (`LUMNIA_DB`, default
`data/lumnia.db`). Reports are stored **with the original upload bytes**, so
any stored file can be re-analyzed as the pipeline improves.

All classification is heuristic and value-based — no sheet names, no
file-specific special cases.

## Run / test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000      # interactive docs at /docs
pytest -q
```

## Roadmap

1. ✅ Upload → sheet inventory + header heuristic
2. ✅ Orientation detection + tidy-table extraction (incl. panels/bands, year axes)
3. ✅ Profiling + metrics over the tidy tables
4. ✅ Deterministic checks — self-auditing reconciliation, fail honest
5. ✅ Persistence + stored-analyses API (findings audit endpoint)

Next candidates: totals-row detection (exclude summary rows from row-level
checks), a frontend over the stored-analyses API, `.ods` support.
