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
