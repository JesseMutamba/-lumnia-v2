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


class AnalyzeResponse(BaseModel):
    filename: str
    n_sheets: int
    sheets: List[SheetReport]


class HealthResponse(BaseModel):
    status: str
    service: str
