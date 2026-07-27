"""Pydantic response schemas for the API surface."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class TidyTable(BaseModel):
    columns: List[str]
    records: List[Dict[str, Any]]
    n_records: int
    n_columns: int
    column_types: Optional[List[Dict[str, Any]]] = None
    header_rows: Optional[List[int]] = None
    summary: Optional[Dict[str, Any]] = None
    checks: Optional[List[Dict[str, Any]]] = None
    # Step 6 — bounded EDA facts computed on the full extracted columns
    eda: Optional[Dict[str, Any]] = None
    # Step 8 — semantic schema + computed story metrics (storytelling engine)
    semantics: Optional[Dict[str, Any]] = None
    # 1-based sheet rows detected as summary/totals rows (derived, not data)
    total_rows: Optional[List[int]] = None


class SheetReport(BaseModel):
    # Step 1 — inventory
    name: str
    n_rows: int
    n_cols: int
    n_nonempty_rows: int
    n_nonempty_cols: int
    fill_ratio: float
    header_row: Optional[int] = None
    preview: List[List[Optional[Any]]]
    # Step 2 — orientation + extraction
    orientation: str
    orientation_confidence: float
    orientation_reason: str
    tidy: Optional[TidyTable] = None
    # side-by-side tables: orientation == "multi", one entry per panel
    panels: Optional[List[Dict[str, Any]]] = None


class AnalyzeResponse(BaseModel):
    # Step 5 — persisted analyses carry their storage id
    id: Optional[str] = None
    filename: str
    n_sheets: int
    sheets: List[SheetReport]
    # Step 6 — ranked narrative insights aggregated across all tables' EDA
    insights: Optional[List[Dict[str, Any]]] = None
    # Step 7 — the semantic business model (role-tagged series + breakdowns)
    model: Optional[Dict[str, Any]] = None
    # journal engine — deep audit of contract-matching dual cash journals
    # (V1–V8 findings, code × month aggregation, tie-out); None otherwise
    journal: Optional[Dict[str, Any]] = None
    # Layer 3 — AI-written narrative (generated on demand, cached with the
    # report; every figure in it comes from the deterministic pipeline)
    narrative: Optional[Dict[str, Any]] = None
    # Step 8 — the workbook's best storytelling table: schema + computed
    # metrics + honest gaps (what the brief's staples would need)
    story: Optional[Dict[str, Any]] = None
    # ...and every qualifying table's story, ranked (story == stories[0]);
    # brief questions may be answered by any of them
    stories: Optional[List[Dict[str, Any]]] = None
    # Phase 2 — the user's intake answers and the matched metric plan
    brief: Optional[Dict[str, Any]] = None
    plan: Optional[Dict[str, Any]] = None
    # Publish lifecycle — persisted per-finding decisions keyed by the
    # stable finding id: {fid: {"decision": ..., "decided_at": iso}}
    decisions: Optional[Dict[str, Any]] = None
    # Config-first mapping: pinned role -> series references, provenance
    # (manual/inherited) and the reconciliation result that admitted it
    mapping: Optional[Dict[str, Any]] = None


class AnalysisMeta(BaseModel):
    """One row in the stored-analyses listing (no report payload)."""
    id: str
    filename: str
    uploaded_at: str
    reran_at: Optional[str] = None
    size_bytes: int
    n_sheets: int
    # client workspace this analysis belongs to (None = unassigned)
    client: Optional[str] = None
    # 'intake' = submitted by the client from their hub (None = analyst upload)
    origin: Optional[str] = None
    # findings (mismatched + unverified) still waiting on a decision
    open_findings: Optional[int] = None
    # reconciliation rollup for the Command Center row
    checks_ok: Optional[int] = None
    checks_total: Optional[int] = None
    # total |delta| of mismatched relations — the money number on Home
    at_stake: Optional[float] = None
    # publish state: latest published version (None = never published) and
    # whether decisions have moved since — both derived, never stored
    published_version: Optional[int] = None
    stale: Optional[bool] = None


class DecisionsRequest(BaseModel):
    """Batch decision update: {finding_id: "open"|"approved"|"flagged"}."""
    decisions: Dict[str, str]


class FindingsResponse(BaseModel):
    """Workbook-level audit: every reconciliation check across all sheets
    and panels, mismatches ranked by money impact."""
    id: str
    filename: str
    n_mismatched_relations: int
    n_verified_relations: int
    n_unverified_relations: int
    total_abs_delta: float
    findings: List[Dict[str, Any]]
    verified: List[Dict[str, Any]]
    # totals rows whose structure we could not model — reported, not guessed
    unverified: List[Dict[str, Any]]


class CellTraceResponse(BaseModel):
    """Cited cells re-read from the ORIGINAL uploaded bytes — the audit
    trail behind one dashboard figure. ``raw`` is the value exactly as the
    file holds it; ``value`` is what the pipeline's coercion makes of it.
    Nothing here is echoed from the stored report."""
    id: str
    filename: str
    sheet: str
    # [{ref, raw, value}] in request order
    cells: List[Dict[str, Any]]
    # a small surrounding slice for visual context:
    # {row_start (1-based), col_letters, rows}
    excerpt: Dict[str, Any]


class DeleteResponse(BaseModel):
    id: str
    deleted: bool


class StatsResponse(BaseModel):
    """Usage roll-up for the traction view — the numbers the memo needs,
    computed from stored metadata rather than run by hand in SQL."""
    total_analyses: int
    total_sheets: int
    total_bytes: int
    n_clients: int
    days_active: int
    first_upload: Optional[str] = None
    last_upload: Optional[str] = None
    by_day: List[Dict[str, Any]]
    by_week: List[Dict[str, Any]]
    by_client: List[Dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    service: str
    # whether the AI narrative layer is configured (ANTHROPIC_API_KEY set)
    narrative_ready: bool = False


class PortalClient(BaseModel):
    name: str
    slug: str


class PortalMe(BaseModel):
    """Who a client session belongs to — derived server-side, never claimed."""
    client: PortalClient
    email: str


class DeliverableMeta(BaseModel):
    """One row in the client hub. Deliberately excludes source_ref: ids in,
    curated payloads out — storage keys and snapshot internals never list."""
    id: str
    title: str
    kind: str
    group: Optional[str] = None
    version: int
    published_at: Optional[str] = None
    status: Optional[str] = None


class ComposeReportRequest(BaseModel):
    """Compose the operations report from pipeline-computed blocks.
    Empty ``blocks`` means every block the data supports. ``title``
    overrides the report's display title (page <h1> and hub deliverable
    name); empty means the filename-derived default."""
    blocks: List[str] = []
    lang: str = "en"
    title: Optional[str] = None
