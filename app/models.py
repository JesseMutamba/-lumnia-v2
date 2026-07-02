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


class AnalysisMeta(BaseModel):
    """One row in the stored-analyses listing (no report payload)."""
    id: str
    filename: str
    uploaded_at: str
    reran_at: Optional[str] = None
    size_bytes: int
    n_sheets: int


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


class HealthResponse(BaseModel):
    status: str
    service: str
