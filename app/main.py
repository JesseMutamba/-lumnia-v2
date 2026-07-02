"""Lumnia v2 API — FastAPI app + routes.

Endpoints
---------
* ``GET  /health``  — liveness.
* ``POST /analyze`` — upload CSV/Excel (form field ``file``) -> per-sheet
  inventory (Step 1) + orientation & tidy extraction (Step 2).
"""
from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile

from .models import AnalyzeResponse, HealthResponse, SheetReport
from .pipeline.ingest import read_upload
from .pipeline.orient import orient_sheet
from .pipeline.profile import profile_sheet

app = FastAPI(title="Lumnia v2", version="0.2.0")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="lumnia-v2")


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile = File(...)) -> AnalyzeResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload.")

    try:
        sheets = read_upload(content, file.filename or "")
    except Exception as exc:  # unreadable file -> fail honest, don't 500 blindly
        raise HTTPException(
            status_code=422,
            detail=f"Could not read '{file.filename}': {exc}",
        )

    reports = []
    for name, df in sheets.items():
        prof = profile_sheet(name, df)
        orient = orient_sheet(df)
        reports.append(
            SheetReport(
                **prof,
                orientation=orient["orientation"],
                orientation_confidence=orient["confidence"],
                orientation_reason=orient["reason"],
                tidy=orient["tidy"],
            )
        )

    return AnalyzeResponse(
        filename=file.filename or "upload",
        n_sheets=len(reports),
        sheets=reports,
    )
