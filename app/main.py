"""Lumnia v2 API — FastAPI app + routes.

Endpoints
---------
* ``GET    /health``               — liveness.
* ``POST   /analyze``              — upload CSV/Excel (form field ``file``) ->
  full analysis (Steps 1-4), persisted; the response carries its ``id``.
* ``GET    /analyses``             — list stored analyses (metadata only).
* ``GET    /analyses/{id}``        — full stored report.
* ``GET    /analyses/{id}/findings`` — workbook-level audit: every discovered
  relation across all sheets/panels, mismatches ranked by money impact.
* ``POST   /analyses/{id}/rerun``  — re-run the *current* pipeline on the
  stored original bytes (the pipeline improves step by step; old uploads
  benefit without re-uploading).
* ``DELETE /analyses/{id}``        — remove an analysis and its stored file.
"""
from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile

from . import storage
from .findings import aggregate_findings
from .models import (
    AnalysisMeta,
    AnalyzeResponse,
    DeleteResponse,
    FindingsResponse,
    HealthResponse,
    SheetReport,
)
from .pipeline.ingest import read_upload
from .pipeline.orient import orient_sheet
from .pipeline.profile import profile_sheet

app = FastAPI(title="Lumnia v2", version="0.5.0")


def run_pipeline(content: bytes, filename: str) -> AnalyzeResponse:
    """Steps 1-4 over one uploaded file. Raises HTTPException on unreadable input."""
    try:
        sheets = read_upload(content, filename)
    except Exception as exc:  # unreadable file -> fail honest, don't 500 blindly
        raise HTTPException(
            status_code=422,
            detail=f"Could not read '{filename}': {exc}",
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
                panels=orient.get("panels"),
            )
        )

    return AnalyzeResponse(
        filename=filename or "upload",
        n_sheets=len(reports),
        sheets=reports,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="lumnia-v2")


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile = File(...)) -> AnalyzeResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload.")

    result = run_pipeline(content, file.filename or "")
    result.id = storage.save_analysis(
        result.filename, content, result.model_dump())
    return result


@app.get("/analyses", response_model=list[AnalysisMeta])
def list_analyses() -> list[AnalysisMeta]:
    return [AnalysisMeta(**meta) for meta in storage.list_analyses()]


@app.get("/analyses/{analysis_id}", response_model=AnalyzeResponse)
def get_analysis(analysis_id: str) -> AnalyzeResponse:
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    return AnalyzeResponse(**report)


@app.get("/analyses/{analysis_id}/findings", response_model=FindingsResponse)
def get_findings(analysis_id: str) -> FindingsResponse:
    """The workbook-level audit: every discovered relation across all sheets
    and panels, with mismatches ranked by total money impact."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    agg = aggregate_findings(report)
    return FindingsResponse(id=analysis_id, filename=report["filename"], **agg)


@app.post("/analyses/{analysis_id}/rerun", response_model=AnalyzeResponse)
def rerun_analysis(analysis_id: str) -> AnalyzeResponse:
    stored = storage.get_content(analysis_id)
    if stored is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    filename, content = stored
    result = run_pipeline(content, filename)
    result.id = analysis_id
    storage.update_report(analysis_id, result.model_dump())
    return result


@app.delete("/analyses/{analysis_id}", response_model=DeleteResponse)
def delete_analysis(analysis_id: str) -> DeleteResponse:
    if not storage.delete_analysis(analysis_id):
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    return DeleteResponse(id=analysis_id, deleted=True)
