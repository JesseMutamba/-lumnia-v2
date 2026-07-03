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

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from . import auth, narrative, storage
from .findings import aggregate_findings
from .models import (
    AnalysisMeta,
    AnalyzeResponse,
    DeleteResponse,
    FindingsResponse,
    HealthResponse,
    SheetReport,
)
from .pipeline.celltypes import grid_kinds
from .pipeline.eda import generate_insights
from .pipeline.model import build_model
from .pipeline.ingest import read_upload
from .pipeline.orient import orient_sheet
from .pipeline.profile import profile_sheet

app = FastAPI(title="Lumnia v2", version="0.5.0")

# Reject uploads beyond this size with 413 — bigger files need a real queue,
# not a synchronous request.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@app.middleware("http")
async def require_password(request: Request, call_next):
    """Gate everything behind the shared password when one is configured.

    No password set (local dev, tests) -> pass through unchanged. Otherwise
    a valid session cookie is required; browsers hitting the app get the login
    page, API calls get 401.
    """
    if auth.password() is None or request.url.path in auth.PUBLIC_PATHS \
            or request.url.path.startswith("/share/"):
        # /share/{token} is deliberately public: the unguessable token IS the
        # credential, and the routes behind it are read-only.
        return await call_next(request)
    if auth.valid_token(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": "Authentication required."}, status_code=401)


def _secure(request: Request) -> bool:
    return (request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https")


@app.get("/login", include_in_schema=False)
def login_page() -> HTMLResponse:
    return HTMLResponse(auth.LOGIN_HTML.replace("__ERROR__", ""))


@app.post("/login", include_in_schema=False)
def login(request: Request, password: str = Form(...)):
    if not auth.check_password(password):
        return HTMLResponse(
            auth.LOGIN_HTML.replace("__ERROR__", "Incorrect password."),
            status_code=401)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.MAX_AGE,
                    httponly=True, samesite="lax", secure=_secure(request))
    return resp


@app.get("/logout", include_in_schema=False)
def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


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
        try:
            kinds = grid_kinds(df)           # one classification pass per sheet
            prof = profile_sheet(name, df, kinds=kinds)
            orient = orient_sheet(df, kinds=kinds)
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
        except Exception as exc:
            # One pathological sheet must not sink the whole workbook: report
            # it as an error honestly and keep going.
            reports.append(SheetReport(
                name=name, n_rows=int(df.shape[0]), n_cols=int(df.shape[1]),
                n_nonempty_rows=0, n_nonempty_cols=0, fill_ratio=0.0,
                header_row=None, preview=[],
                orientation="error", orientation_confidence=0.0,
                orientation_reason=f"analysis failed: {type(exc).__name__}: {exc}",
                tidy=None, panels=None,
            ))

    # Step 6: fold every table's EDA facts into ranked workbook insights.
    eda_results = []
    for r in reports:
        if r.tidy and r.tidy.eda:
            eda_results.append({**r.tidy.eda, "sheet": r.name})
        for p in r.panels or []:
            t = p.get("tidy")
            if t and t.get("eda"):
                eda_results.append({**t["eda"], "sheet": r.name})

    return AnalyzeResponse(
        filename=filename or "upload",
        n_sheets=len(reports),
        sheets=reports,
        insights=generate_insights(eda_results) or None,
        model=build_model(reports),
    )


_INDEX = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """The single-page UI: upload, history, per-sheet report, audit.

    no-cache so browsers revalidate on every load — the UI evolves with the
    backend and a cached page against a newer API is a confusing failure.
    """
    return FileResponse(_INDEX, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="lumnia-v2",
                          narrative_ready=narrative.available())


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile = File(...)) -> AnalyzeResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is {len(content) / 1e6:.1f} MB; the limit is "
                   f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB.")

    # Identical bytes already analyzed -> return the stored analysis instead
    # of silently growing the library with duplicates.
    existing = storage.find_by_content(content)
    if existing is not None:
        report = storage.get_report(existing)
        if report is not None:
            return AnalyzeResponse(**report)

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


@app.post("/analyses/{analysis_id}/client")
async def assign_client(analysis_id: str, request: Request) -> dict:
    """Assign the analysis to a client workspace ({"client": "PVAK"};
    empty string clears the assignment)."""
    body = await request.json()
    client = (body.get("client") or "").strip()[:60]
    if not storage.set_client(analysis_id, client):
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    return {"id": analysis_id, "client": client or None}


@app.post("/analyses/{analysis_id}/share")
def create_share(analysis_id: str) -> dict:
    """Mint (or return) the read-only share link for an analysis."""
    token = storage.create_share(analysis_id)
    if token is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    return {"token": token, "url": f"/share/{token}"}


@app.delete("/analyses/{analysis_id}/share")
def revoke_share(analysis_id: str) -> dict:
    """Revoke the share link; the URL stops working immediately."""
    return {"revoked": storage.revoke_share(analysis_id)}


def _shared_report(token: str) -> dict:
    analysis_id = storage.resolve_share(token)
    report = storage.get_report(analysis_id) if analysis_id else None
    if report is None:
        raise HTTPException(status_code=404, detail="This link is no longer active.")
    return report


@app.get("/share/{token}", include_in_schema=False)
def share_page(token: str) -> FileResponse:
    """The read-only client view (same SPA; it detects /share/ and hides
    upload, library, rerun and delete). 404 for dead tokens."""
    _shared_report(token)
    return FileResponse(_INDEX, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


@app.get("/share/{token}/report", response_model=AnalyzeResponse)
def share_report(token: str) -> AnalyzeResponse:
    return AnalyzeResponse(**_shared_report(token))


@app.get("/share/{token}/findings", response_model=FindingsResponse)
def share_findings(token: str) -> FindingsResponse:
    report = _shared_report(token)
    agg = aggregate_findings(report)
    return FindingsResponse(id="shared", filename=report["filename"], **agg)


@app.post("/analyses/{analysis_id}/narrative")
def make_narrative(analysis_id: str, lang: str = "en") -> dict:
    """Layer 3: generate (and cache) the AI-written executive narrative.

    The pipeline computes every figure; Claude only phrases them. Without an
    ANTHROPIC_API_KEY the feature is honestly unavailable — 503, no fallback
    prose pretending to be AI.
    """
    if not narrative.available():
        raise HTTPException(
            status_code=503,
            detail="AI narrative is not configured on this server "
                   "(set the ANTHROPIC_API_KEY secret).")
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    audit = aggregate_findings(report)
    try:
        result = narrative.generate_narrative(
            report, audit, lang="fr" if lang == "fr" else "en")
    except narrative.NarrativeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    report["narrative"] = result
    storage.update_report(analysis_id, report)
    return result


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
