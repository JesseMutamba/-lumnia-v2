# Lumnia v2 — System Rebuild Specification

A complete blueprint for rebuilding the platform from scratch. Everything in
this document reflects the shipped system as of 2026-07-26 (post PR #55 +
Insights tab). Where a rule is load-bearing it is marked **INVARIANT** —
break one and the product's reason to exist breaks with it.

---

## 1. What Lumnia is

A B2B financial/operational intelligence platform for operators whose data
lives in messy spreadsheets. Clients upload Excel/CSV workbooks in any shape;
Lumnia audits them (totals re-computed, contradictions quantified), extracts
a business model, and returns decision-ready dashboards and client
deliverables. **Trust is the product** — a fabricated metric is a
company-ending bug.

Anchor client shape: French-language, multi-sheet, cross-tab agribusiness
workbooks (palm oil, DRC). Merged cells, banner rows, totals rows, accented
labels, "1 234,5" number formats, month-name headers ("janv. 2025"). That is
the **normal case**, not the edge case.

### Core invariants

1. **LLMs narrate; deterministic code computes.** Any number shown to a
   client traces to deterministic computation over real uploaded data. AI
   (Anthropic API) is optional and only ever phrases already-computed
   figures (`app/narrative.py`).
2. **Fail honest.** If the data can't support a metric, the metric does not
   render — it shows as a declared gap ("needs: 13 months of dated rows"),
   never a number, never a placeholder.
3. **Provenance or silence.** Figures carry the exact source cells (A1 refs)
   they were computed from; a figure whose cells can't be named carries no
   citation — a partial or wrong citation is worse than none.
4. **The exec/published surface carries no file structure** — no sheet
   names, no cell refs, no line-item labels. The analyst keeps provenance;
   the exec gets verified aggregates.
5. **Client deliverables are self-contained and script-free** — no external
   fetches (fonts embedded as data URIs), no `<script>`, charts as inline
   SVG, values baked at compose time. They must render identically offline,
   forever.
6. **No frontend math beyond formatting.** The browser formats and displays;
   it never computes a value.

---

## 2. Stack

- **Backend:** Python 3.11, FastAPI + Pydantic, pandas + openpyxl (write) /
  python-calamine (fast read, openpyxl fallback). No other deps.
- **Persistence:** SQLite via stdlib `sqlite3` (`app/storage.py`), one file
  DB + a files dir for stored deliverables. Env: `LUMNIA_DB`,
  `LUMNIA_FILES`.
- **Frontend:** ONE file — `app/static/index.html` (~7.1k lines). CSS custom
  properties + vanilla JS + hand-built SVG. No framework, no build step.
- **Auth:** single shared password (`LUMNIA_PASSWORD`), session cookie,
  middleware-gated. Public paths: `/published/{token}`, `/portal/{token}`,
  `/share/…`, `/login`, `/health` — the unguessable token is the credential.
- **AI:** `ANTHROPIC_API_KEY` optional; absent = the narrative feature
  simply doesn't appear.
- **Deploy:** Docker (python:3.11-slim), uvicorn, `$PORT`. Render blueprint
  (`render.yaml`, autoDeploy on main, persistent disk at `/data`) or Fly.io
  (`fly.toml`, volume `lumnia_data`).

---

## 3. Pipeline (app/pipeline/), in execution order

Upload → `run_pipeline(content, filename)` in `app/main.py`:

### 3.1 ingest.py — bytes → raw grids
- `read_upload(content, filename) -> Dict[sheet_name, DataFrame]`.
- Excel: every sheet, `header=None, dtype=object` — **positional identity**:
  `df.iat[i, j]` == sheet cell (row i+1, col j). CSV: `skip_blank_lines=False`,
  `encoding="utf-8-sig"` (strip BOM), sheet named by filename stem.
- Caps: 25 MB upload, 1.5 M total cells (422 with bilingual error).

### 3.2 celltypes.py — per-cell classification
- `cell_kind(v)` → BLANK / NUMBER / DATE / TEXT / …; `grid_kinds(df)` one
  pass per sheet. French numerals ("1 234,5"), month-name strings
  ("Février 2026", "mars-26") classify as NUMBER/DATE. "Q1 Total",
  "2026" alone, "Mai" alone must NOT become dates.

### 3.3 coerce.py — normalization
- `coerce_value` / `coerce_number` / `coerce_date`: "1 234,5" → 1234.5,
  "Jan 2026" → "2026-01-01". Pure, per-value.

### 3.4 orient.py — the heart: classify + extract each sheet
- `classify(df, kinds)` → orientation: `tidy` (header row + records),
  `matrix` (cross-tab: label cols + period cols under a date/year header
  row), `form`, `unknown`; plus confidence + reason.
- **Panels:** side-by-side tables split on blank columns (≥2 cols each);
  **bands:** vertically stacked tables split on blank rows (fallback when
  the whole sheet declines). Each slice classified independently;
  `_finalize_cells(res, row_off, col_off)` re-adds slice offsets to every
  positional output.
- **Tidy extraction** (`_extract_tidy_table`): multi-row header merge
  (" / "-joined names), label-column forward-fill for merged cells
  (`_ffill_label_cols` — and a `ghost_rows` map: (column, row-ordinal)
  positions whose value was ffilled, i.e. physically blank), streaming
  column stats, full coerced columns, `row_numbers` (1-based sheet rows),
  `col_map` (name → sheet col), totals-row detection, reconciliation
  checks, summary chart, EDA, semantics.
- **Matrix extraction** (`_extract_matrix`): label cols + period cols;
  long-form records (labels, period, value); `series_values[label][period_idx]`
  summed across rows sharing a label key; **`series_cells[label][period_idx]`
  = [[1-based row, 0-based col], …]** — the provenance of every summed cell.
- **A1 machinery:** `_xl_col(j)` 0-based col → letters;
  `_finalize_cells` converts checks-mismatch (row, col) and chart
  `series_all[].cells` int pairs into absolute A1 strings with panel/band
  offsets. Runs exactly once on every result path.

### 3.5 checks.py — reconciliation (the audit)
- Re-computes every discoverable relation (row totals, column totals,
  cross-footing) over the numeric columns. A mismatch carries
  `{row (1-based), col, label, expected, actual, delta}` → finalized to a
  real A1 `cell`. Findings: `{kind, formula, target, inputs, n_checked,
  n_matched, n_mismatched, total_abs_delta, mismatches (≤10), status,
  fix_confidence}`. Oracle style: plant a 1,250 delta → exactly one finding
  naming the exact cell ("D3"; "H3" under a panel offset).

### 3.6 charts.py — chart-ready series at analysis time
- `timeseries_chart(periods, series_values, axis, series_cells=None)`:
  top-5 plotting series + "Other" fold; **`series_all`** (every series,
  ≤24) for the model layer — entries `{label, values[, cells]}` where
  `cells[i]` is the list of source refs behind `values[i]`.
- `year_series_chart(numeric_cols, totals_idx, row_numbers, col_map,
  ghost_rows)`: a tidy table with a bare year column becomes a year-axis
  series (rows summed per year). **Ghost rule (INVARIANT):** any period a
  forward-filled (physically blank) cell contributed to gets NO cells entry
  — citing a blank, or only the real subset of a sum, is a false proof.
- `profile_chart`: per-column sums over the longest numeric run, totals
  excluded, endpoint Total column dropped.

### 3.7 semantics.py — the storytelling engine
- `detect_schema` + `compute_story` per table: headline, by_<dim>
  breakdowns, trend, MoM/YoY, movers (top/bottom entities; TOTAL/CUMUL
  rows never appear as movers), low_stock; every metric computed, gaps
  declared (`{metric, reason, requires}`).
- `suggest_brief` proposes questions; `plan_from_brief(brief, stories)`
  matches every brief question against ALL stories → per-question
  `{question, status: answerable|partial|unanswerable|unmatched, metrics
  (qualified "s{story}:{id}"), sheet, missing[{metric, requires}]}`.
  **Round-trip oracle (INVARIANT):** every suggested question fed back
  through `plan_from_brief` is answerable or partial, never unmatched.

### 3.8 eda.py — bounded per-table facts → `generate_insights` ranked
  workbook insights (bilingual message/message_fr sentences).

### 3.9 model.py — the business model (role-tagging)
- `ROLE_PATTERNS` (order matters): price → revenue → capex → budget → opex
  → volume → area → headcount. French first. Rate rows ("Cost / tonne")
  are never level series; TOTAL/CUMUL series only carry a role when no
  clean series claims it.
- Year spine: the year-axis chart with most roles; other year charts
  supplement missing roles, re-indexed by **exact period-string equality**
  (`_reindex`; duplicate periods join as None — "which column is truth?" is
  unknowable). `MIN_XSHEET_OVERLAP = 3` aligned cells or it's a gap.
  `dropped_outside_axis` declares excluded foreign cells.
- Monthly block: date-axis chart with most roles = actuals spine.
  **Plan-shaped sheets (BUDGET/PLAN/PRÉVISIONS/FORECAST/PROJECTIONS in the
  sheet name) are a separate pool** — compared to actuals, never merged
  (a plan presented as an actual is a fabrication).
- `derive_metrics`: margin, margin_pct, opex_per_volume(_out),
  revenue_per_volume/area, volume_ratio (output÷input extraction rate),
  opex_budget_variance_pct, net_cash + cumulative walk (goes None from the
  first gap — a partial walk claims a balance nobody computed). Cell-by-cell,
  both inputs present or None.
- `_plan_vs_actual`: plan re-indexed onto the actuals axis; volume plans
  pair by label identity tokens (CPO plan meets CPO actual, never FFB);
  per-role `{plan, plan_label, plan_sheet, plan_cells, pct_of_plan,
  plan_total, actual_total, pct_of_plan_total}`; <3 aligned pairs → honest
  gap.
- `_unit_cost_budget`: "$/t actual vs budget" — only when the year spine IS
  budget-shaped AND both sides derive the same basis AND actuals sit in one
  plan year. `{basis, target, target_period, variance_pct[],
  target_sources: {role: {sheet, label, cells}}}`.
- `_public_metrics`: `{label, sheet, values[, cells][, dropped_outside_axis]}`
  per role. Cells ride the whole way from extraction.
- mapping.py: config-first role pinning — deterministic override of the
  heuristics, but **never trusted until reconciled** (margin_identity:
  revenue−opex=margin within 1%; revenue_identity: price×volume within 2%;
  budget_scale within 60%). Unverifiable mapping = `ok: None`, manual pin
  allowed, never auto-inherited.

### 3.10 journal.py — contract-matching dual cash journals get a deep audit
  (rules V1–V8: matching, balances, codes, duplicates), code×month
  re-aggregation from dates (Excel SUMIF ranges are not trusted), exec
  summary block (destinations, exceptions with USD at stake).

### 3.11 Assembly (`main.py run_pipeline`)
- Per sheet: `grid_kinds` → `profile_sheet` (inventory + 6×12 preview) →
  `orient_sheet`. One pathological sheet reports `orientation: "error"`
  honestly and never sinks the workbook.
- `AnalyzeResponse`: `{id, filename, n_sheets, sheets[SheetReport],
  insights, model, journal, narrative, story, stories (ranked, spine
  first), brief, plan, decisions, mapping}`. Stored as one JSON blob
  **next to the original upload bytes** (sha256-deduped) — reruns re-derive
  everything from the stored bytes.

---

## 4. Storage (app/storage.py)

SQLite tables (approx):
- `analyses(id, filename, uploaded_at, reran_at, size_bytes, sha256,
  content BLOB, report JSON, client, origin, mapping JSON)` — **the original
  bytes live here (INVARIANT: trace re-reads them, deletes remove them)**.
- `published(analysis_id UNIQUE, token, version, snapshot JSON,
  published_at)` — republish overwrites + bumps version.
- `deliverables(id, client_id, kind 'dashboard'|'file', title, grp, version,
  status, source_ref, …)` — `source_ref` = analysis_id (dashboard) or a
  files-dir path (file). UNIQUE(kind, source_ref). `source_ref` never
  serializes to clients.
- clients / portal users (email login links) / access requests / decisions.

---

## 5. API surface (all `/analyses/*` operator-gated by the password wall)

- `POST /analyze` (multipart `file`) → full report, persisted.
- `GET /analyses`, `GET /analyses/{id}`, `DELETE /analyses/{id}`.
- `GET /analyses/{id}/findings` — workbook audit rollup, mismatches ranked
  by |Δ|; stable sha1 finding ids.
- `POST /analyses/{id}/decisions` — `{fid: open|approved|flagged}`;
  decisions survive rerun for findings whose id still exists.
- `POST /analyses/{id}/rerun` — current pipeline over stored bytes; brief
  survives; plan re-matched; approvals/decisions kept where still valid;
  pinned mapping survives only if it still resolves and reconciles.
- `GET /analyses/{id}/cells?sheet=&refs=B3,C4` — **the trace endpoint**:
  re-reads the ORIGINAL bytes through the same ingest path; per ref
  `{ref, raw, value}` (both clipped at 300 chars, ellipsis marks the clip);
  plus an excerpt slice `{row_start, col_letters, rows}` that ALWAYS
  includes column A (the label column) + the citation neighborhood, ≤10×12.
  ≤40 refs/request (the frontend chunks longer lists). 400/404 fail honest.
  DEBT: re-parses the workbook per click — cache when books grow.
- Brief flow: `POST /analyses/{id}/brief` (role/goals/questions/cadence →
  plan), `POST /analyses/{id}/plan` (approve metric ids),
  `GET /analyses/{id}/brief-suggestion`.
- Publish: `GET/POST/DELETE /analyses/{id}/publish` → frozen exec snapshot
  + share token; `GET /published/{token}` serves the snapshot verbatim.
- Deliverables: `POST /analyses/{id}/brief-report`,
  `POST /analyses/{id}/compose-report` (`{blocks: [], lang}` — empty =
  every block the data supports; unsupported blocks are DECLARED skipped);
  portal: `/portal/{token}` login, `/portal/me|deliverables|intake`, signed
  URLs served with CSP `sandbox allow-scripts`.
- `POST /analyses/{id}/narrative?lang=` — optional AI phrasing, cached in
  the report.
- `GET /health`, `GET /stats`.
- Errors are bilingual one-liners: `"EN text · FR text"`.

---

## 6. The exec snapshot (app/snapshot.py)

`build_exec_snapshot(report)` freezes exactly what the exec page shows:
audit counts (never per-finding detail), the model **stripped of
`source_sheet`, per-metric `sheet` and `cells`, `plan_cells`,
`target_sources`, and `breakdowns`** (line-item labels), journal exec block,
one fallback story chart (no sheet name). The public payload carries no
file structure (INVARIANT #4).

---

## 7. Frontend (app/static/index.html)

### Design system
- Tokens in `:root`: paper/surface `#f4f0e8/#f6f3ec`, card `#fbf9f3`,
  border `#ddd4bf`, hairline `#e8e1cf`, ink `#201d17` (+ink-2/-3), ONE gold
  accent `#a8821f` (strong `#c9992a`, tint `#f3ead2`), critical `#b03a2e`,
  good `#0a7d33`. `--serif: "Source Serif 4"`, `--mono: "IBM Plex Mono"`
  (Google Fonts link for the app itself). No card shadows, radius 0.
  Kickers: mono 10px uppercase letterspaced.
- **Every user-facing string lives in `STR = {en: {…}, fr: {…}}`** — both
  languages, always, same relative position. `T = STR[LANG]`; LANG from
  localStorage else navigator.language. Values are strings or arrow
  functions of interpolated parts.

### Structure
- Globals: `current` (analysis id), `REPORT`, `AUDIT`, `CHARTS`, `TRACES`,
  `SHEET_IDX`, `TAB`/`EXEC_TAB`/`PAGE`/`MODE`. `renderAll()` dispatches;
  content is innerHTML template strings; `attach*()` functions wire events
  post-render. `rerender(fn)` wraps in a View Transition.
- **Analyst tabs:** Workbench (4-job rail: review findings → decide →
  brief/compose → publish, with a review modal), Overview, Briefing (brief
  wizard → plan approval → story dashboard), Financials, Production,
  What-if (scenario levers + Monte Carlo — exploration only, never mutates
  stored figures), Data (charts/profile/sheet cards), Journal, Audit.
- **Exec page** (in-app preview + public published page share
  `renderExecBody` so they can never drift): Overview (KPI strip + verdict
  headline), **Briefing** (read-only plan questions + status badges, no
  metric chips — matrix measure labels embed sheet-derived names),
  **Insights** (rule-computed discussion agenda, below), Cost of
  production, Production, What-if. Tab gating = capability gating: a tab
  without data never shows.
- **Click-to-trace:** Production statbar figures (cost-vs-budget,
  plan-attainment) are `.traceable` chips (gold dashed underline + ⌖).
  `traceChip(spec)` registers `{title, sub, items: [{k, sheet, refs}]}`
  during render (never on exec); `openTrace` fetches
  `/analyses/{id}/cells` (chunked ≤40 refs), renders a modal: per-cell
  value chips (raw shown when the file writes it differently, e.g.
  "1 234,5"), the sheet excerpt with cited cells highlighted in gold, a
  jump-to-sheet link, and the footer "values re-read from the file just
  now, never from the stored dashboard". Stale-fetch guard: writes only
  into the scrim its own call created. The attainment chip cites EVERY
  aligned month on both sides or doesn't render (partial citation = false
  proof).
- **Exec Insights rules** (each names its figure; thresholds are product
  judgments): unreconciled relations (any) → cost/t ≥10% over budget →
  plan attainment <90% (or opex >110%) → conversion ≥10% under its own
  average → capital bridge → journal exceptions → investment share ≥40% →
  up to 2 open briefing gaps. Cap 7. Empty agenda says so honestly.
- Findings UI: mismatch cells (A1) shown, corrections CSV export
  (sheet, panel, cell, row, label, current, suggested), decision chips.
- Print report (⎙): builds `#printdoc` (serif body — brand document) from
  data, `window.print()`.

### Client-facing deliverables
- **Composed operations report (app/compose.py):** blocks = kpi,
  plan_vs_actual, unit_cost, conversion, cash, outlook, net_cash,
  breakdowns, monthly_table. Self-contained HTML: fonts embedded as
  data-URI `@font-face` (Source Serif 4 400/600 + IBM Plex Mono 400/500,
  OFL latin subsets in `app/static/fonts/` with licenses, ~95 KB),
  sections as native `<details open>` folds, grouped-bar SVGs with baked
  hover readouts (`.tt` CSS-only reveal + SVG `<title>`), zero `<script>`,
  zero `http(s)://`. Gated like publish: open findings must be decided.
  Versioned per title; recompose bumps version.
- **Brief report (app/brief.py):** the narrative deliverable, same gates.
- Portal hub lists deliverables (ids in, curated payloads out — storage
  keys never serialize).

---

## 8. Testing philosophy & oracles

`python -m pytest -q` — 290 tests, in-memory workbooks only (pandas →
BytesIO → POST /analyze via TestClient; no fixture files on disk). Every
fixture looks like a real French agribusiness workbook: accents, banner
rows, totals rows, merged labels.

Load-bearing oracles to re-create:
- **Checks:** planted delta → exactly one finding with the exact A1 cell;
  panel offset → absolute address.
- **Brief round-trip:** every suggested question is answerable/partial.
- **Matrix:** TOTAL/CUMUL rows excluded from sums; movers never name them.
- **Provenance (tests/test_provenance.py):** planted values → exact A1
  refs; duplicate-label rows list every contributing cell and the value is
  their sum; panel AND band offsets absolute; short plans None-pad
  plan_cells like values; no positions → no cells key; ffilled ghosts
  block the citation; **the anti-fabrication property: re-reading every
  emitted ref from the uploaded bytes reproduces every rendered value**;
  the public snapshot carries no cells/plan_cells/target_sources.
- **Trace (tests/test_trace.py):** raw vs coerced ("1 234,5" → 1234.5);
  every metric value re-derives from the endpoint's own answers; excerpt
  keeps the label column on 14-column sheets; huge cells clipped; honest
  400/404s.
- **Compose (tests/test_compose_report.py):** script-free, self-contained
  (no https:// at all), fonts embedded, `<details>` present, hover
  readouts present, versioning, French decimal commas, blocks skipped =
  declared.
- **Frontend:** driven with Playwright against a scratch uvicorn
  (screenshots; `document.fonts.check` for embedded fonts; hover/fold
  assertions; exec pages assert ZERO sheet names).
- **Demo gate (scripts/demo_check.py):** reset isolated demo-state →
  ingest N× → assert the monthly block, cost-vs-budget with named target
  cells, and that every figure (metrics, target_sources, plan_cells)
  re-derives through the trace endpoint. `--reset-only` = clean slate.

---

## 9. Ops

- `Dockerfile`: slim image, uvicorn `--proxy-headers
  --forwarded-allow-ips="*"` (Secure cookies behind the platform edge),
  `$PORT` fallback 8080.
- Render: blueprint deploy, starter plan + 1 GB disk at `/data` (free plan
  = no disk = every restart wipes analyses). Auto-deploys every merge to
  main. Fly: manual `fly deploy`, volume-backed.
- Password rotation logs everyone out (sessions keyed by password).

---

## 10. Known limits / open items (current watch list)

1. Suggestion-chip phrasing inherits messy headers (answerable but
   awkward); smoothing must not break the round-trip oracle.
2. Cross-WORKBOOK consolidation not built — combine sheets into one
   workbook per engagement before upload.
3. Analyses stored before an engine upgrade need ↺ rerun to grow new
   fields (e.g. provenance cells).
4. `# DEBT:` exec KPI tiles don't surface `dropped_outside_axis`;
   mapping restricts manual `volume_secondary` to identical axes;
   yearly-plan-vs-phased-monthly needs an explicit phasing rule; the trace
   endpoint re-parses the workbook per click (cache when books grow).
5. **FIXED — ghost-VALUES bug:** label-ish forward-filled columns are now
   excluded from numeric series entirely (`label_set` gate in
   `_extract_tidy_table`; oracle:
   `test_ffilled_label_columns_never_become_numeric_series` — one real 500
   can no longer render as 1500). The `ghost_rows` citation suppression
   stays as defense-in-depth for any future path a ghost value could take.

---

## 11. Build order (if remaking from zero)

1. ingest + celltypes + coerce (French formats first) → oracle: exact
   positional identity.
2. orient: classify tidy/matrix, panels/bands, ffill, header merge →
   oracles per fixture shape.
3. checks + A1 finalize → the planted-delta oracle. This is the trust
   backbone; nothing ships before it.
4. charts (series_all + provenance cells) → model (roles, spine,
   plan-vs-actual, unit-cost-vs-budget, derive_metrics) → provenance
   round-trip oracle.
5. storage + API (analyze/findings/decisions/rerun/trace).
6. semantics (schema, story, brief→plan) → round-trip oracle.
7. Frontend analyst view (STR en+fr from day one), then exec view +
   snapshot stripping, then publish/portal/deliverables (script-free
   invariant + its oracle), then click-to-trace.
8. journal engine, narrative (optional AI), demo gate script.

*Green or it didn't happen.*
