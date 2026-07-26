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

import logging
import mimetypes
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

import datetime as _dt

from . import auth, brief, compose, landing, mailer, narrative, storage
from .findings import DECISIONS, aggregate_findings, count_open
from .snapshot import build_exec_snapshot
from .models import (
    AnalysisMeta,
    AnalyzeResponse,
    CellTraceResponse,
    ComposeReportRequest,
    DecisionsRequest,
    DeleteResponse,
    DeliverableMeta,
    FindingsResponse,
    HealthResponse,
    PortalClient,
    PortalMe,
    SheetReport,
    StatsResponse,
)
from .pipeline.celltypes import grid_kinds
from .pipeline.eda import generate_insights
from .pipeline.journal import build_journal_block
from .pipeline.model import attach_plan_progress, build_model, plan_pool_charts
from .pipeline.mapping import (MAPPABLE_ROLES, build_mapped_model,
                               build_mapped_monthly, month_series,
                               plan_charts_from_report, reconcile,
                               resolve, year_series)
from .pipeline.semantics import plan_from_brief, suggest_brief
from .pipeline.coerce import coerce_value
from .pipeline.ingest import merge_engagement, read_upload
from .pipeline.jsonsafe import jsonify
from .pipeline.orient import _xl_col, orient_sheet
from .pipeline.profile import profile_sheet

app = FastAPI(title="Lumnia v2", version="0.5.0")

# Reject uploads beyond this size with 413 — bigger files need a real queue,
# not a synchronous request.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Bytes cap work in transit; this caps work in the pipeline. A dense grid
# past this many cells would hold a worker thread for minutes — refuse
# honestly instead of grinding (~1.5M cells ≈ 90s of pipeline).
MAX_TOTAL_CELLS = 1_500_000


@app.middleware("http")
async def require_password(request: Request, call_next):
    """Gate everything behind the shared password when one is configured.

    No password set (local dev, tests) -> pass through unchanged. Otherwise
    a valid session cookie is required; browsers hitting the app get the login
    page, API calls get 401.
    """
    if auth.password() is None or request.url.path in auth.PUBLIC_PATHS \
            or request.url.path.startswith("/published/") \
            or request.url.path.startswith("/portal/") \
            or request.url.path.startswith("/share/"):
        # /published/{token} and /portal/{token} are deliberately public: the
        # unguessable token IS the credential, and the only thing behind it is
        # frozen exec-only material. Enabling/revoking a portal lives under
        # /clients/... and stays gated. /share/ stays listed so retired links
        # get an honest 404 instead of a login wall.
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
            detail=_bi(f"Could not read '{filename}'",
                       f"Impossible de lire « {filename} »") + f" — {exc}",
        )

    total_cells = sum(int(df.shape[0]) * int(df.shape[1])
                      for df in sheets.values())
    if total_cells > MAX_TOTAL_CELLS:
        raise HTTPException(
            status_code=422,
            detail=_bi(
                f"'{filename}' has {total_cells:,} cells; the limit is "
                f"{MAX_TOTAL_CELLS:,} — split the workbook or remove unused "
                "sheets.",
                f"« {filename} » compte {total_cells:,} cellules ; la limite "
                f"est {MAX_TOTAL_CELLS:,} — scindez le classeur ou retirez "
                "les feuilles inutiles."))

    reports = []
    for name, df in sheets.items():
        try:
            kinds = grid_kinds(df)           # one classification pass per sheet
            prof = profile_sheet(name, df, kinds=kinds)
            orient = orient_sheet(df, kinds=kinds, name=name)
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

    stories = _collect_stories(reports)
    model = build_model(reports)
    # contract-matching workbooks (dual cash journals) get the deep
    # journal audit on top of the generic pipeline
    journal = build_journal_block(sheets)
    # progress vs the projection plan: part-year actuals against the plan
    # year, journal-backed for spend — needs both blocks, so attached here
    attach_plan_progress(model, journal, plan_pool_charts(reports))
    return AnalyzeResponse(
        filename=filename or "upload",
        n_sheets=len(reports),
        sheets=reports,
        insights=generate_insights(eda_results) or None,
        model=model,
        story=stories[0] if stories else None,
        stories=stories or None,
        journal=journal,
    )


MAX_STORIES = 8


def _collect_stories(reports) -> list:
    """Every table's story, ranked by grounding: rows x real groupability
    (dimensions with >= 2 members), with a strong bonus for a time axis.
    The first entry is the workbook's spine; questions may land on any."""
    scored = []
    for r in reports:
        candidates = []
        if r.tidy and r.tidy.semantics:
            candidates.append((r.tidy.n_records, r.tidy.semantics))
        for p in r.panels or []:
            t = p.get("tidy")
            if t and t.get("semantics"):
                candidates.append((t.get("n_records", 0), t["semantics"]))
        for n_rows, sem in candidates:
            schema = sem["schema"]
            real_dims = sum(1 for d in schema["dimensions"]
                            if d["cardinality"] >= 2)
            score = n_rows * (1 + real_dims) * (3 if schema["time"] else 1)
            scored.append((score, {"sheet": r.name, "schema": schema,
                                   **sem["story"]}))
    scored.sort(key=lambda x: -x[0])
    return [s for _score, s in scored[:MAX_STORIES]]


def _bi(en: str, fr: str) -> str:
    """User-facing error copy: one line, both languages — the UI surfaces
    these raw and the reader may work in either. EN leads, FR rides, same
    convention as the sign-in answers."""
    return f"{en} · {fr}"


_INDEX = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def index(request: Request):
    """The root wears two faces: the workspace for a signed-in operator (or
    dev mode with no password), the public landing page for everyone else.
    Anonymous visitors get the pitch, never a login wall.

    no-cache so browsers revalidate on every load — the UI evolves with the
    backend and a cached page against a newer API is a confusing failure.
    """
    if (auth.password() is None
            or auth.valid_token(request.cookies.get(auth.COOKIE))):
        return FileResponse(_INDEX, media_type="text/html",
                            headers={"Cache-Control": "no-cache"})
    return HTMLResponse(landing.LANDING_HTML,
                        headers={"Cache-Control": "no-cache"})


@app.get("/landing-shot.jpg", include_in_schema=False)
def landing_shot() -> FileResponse:
    """PUBLIC. The landing page's product screenshot (demonstration data)."""
    return FileResponse(_INDEX.parent / "landing-shot.jpg",
                        media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="lumnia-v2",
                          narrative_ready=narrative.available())


def _check_upload(content: bytes, label: str) -> None:
    if not content:
        raise HTTPException(status_code=400,
                            detail=_bi("Empty upload.", "Fichier vide."))
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=_bi(
                f"{label} is {len(content) / 1e6:.1f} MB; the limit is "
                f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB.",
                f"« {label} » fait {len(content) / 1e6:.1f} Mo ; la limite "
                f"est {MAX_UPLOAD_BYTES / 1e6:.0f} Mo."))


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile = File(...),
                  plan: Optional[UploadFile] = File(None)) -> AnalyzeResponse:
    content = await file.read()
    _check_upload(content, file.filename or "upload")
    filename = file.filename or ""

    # An optional projections/budget workbook rides along: both files merge
    # into ONE engagement workbook (plan sheets prefixed "PLAN · " so the
    # model's plan pool claims them by name). The merged bytes become the
    # analysis's original — trace re-reads THEM, verbatim.
    if plan is not None:
        plan_content = await plan.read()
        _check_upload(plan_content, plan.filename or "plan")
        try:
            content = await run_in_threadpool(
                merge_engagement, content, filename,
                plan_content, plan.filename or "plan.xlsx")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=_bi(
                f"Could not combine '{filename}' with "
                f"'{plan.filename}'", "Impossible de combiner "
                f"« {filename} » et « {plan.filename} »") + f" — {exc}")
        stem = filename.rsplit(".", 1)[0] or "engagement"
        filename = f"{stem} + plan.xlsx"

    # Identical bytes already analyzed -> return the stored analysis instead
    # of silently growing the library with duplicates.
    existing = storage.find_by_content(content)
    if existing is not None:
        report = storage.get_report(existing)
        if report is not None:
            return AnalyzeResponse(**report)

    # CPU-bound pandas work must not run on the event loop: inline it and
    # one big upload freezes every other request AND the /health check,
    # which lets Fly/Render restart the box mid-analysis.
    result = await run_in_threadpool(run_pipeline, content, filename)
    report = result.model_dump()
    _inherit_mapping(report)          # a verified client mapping carries over
    result = AnalyzeResponse(**report)
    result.id = storage.save_analysis(
        result.filename, content, result.model_dump())
    return result


def _inherit_mapping(report: dict) -> None:
    """Apply the newest stored mapping that resolves on this report AND
    reconciles green against ITS data. Verified-only, both at save time and
    now: a mapping the file itself cannot confirm never spreads to new
    uploads — it stays a per-file manual pin."""
    for cand in storage.recent_mappings():
        roles = (cand["mapping"] or {}).get("roles")
        if not roles:
            continue
        if (cand["mapping"].get("reconciliation") or {}).get("ok") is not True:
            continue
        resolved, _errors = resolve(roles, report)
        if not resolved:
            continue
        rec = reconcile(resolved)
        if rec["ok"] is not True:
            continue
        report["model"] = build_mapped_model(report, resolved)
        report["mapping"] = {
            "roles": roles,
            "provenance": {"kind": "inherited", "from": cand["filename"],
                           "from_id": cand["id"],
                           "at": _dt.datetime.now(_dt.timezone.utc)
                                 .isoformat(timespec="seconds")},
            "reconciliation": rec,
        }
        return


@app.get("/analyses", response_model=list[AnalysisMeta])
def list_analyses() -> list[AnalysisMeta]:
    return [AnalysisMeta(**meta) for meta in storage.list_analyses()]


@app.get("/stats", response_model=StatsResponse)
def get_stats() -> StatsResponse:
    """Usage roll-up behind the shared password — feeds the traction view
    and the memo (uploads over time, per-client counts, active days)."""
    return StatsResponse(**storage.stats())


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


@app.post("/analyses/{analysis_id}/decisions")
def set_decisions(analysis_id: str, body: DecisionsRequest) -> dict:
    """Batch per-finding decisions. Rejects unknown finding ids and unknown
    decision values wholesale — a typo must not half-apply a review."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    agg = aggregate_findings(report)
    known = {f["id"] for f in
             agg["findings"] + agg["unverified"] + agg["verified"]}
    for fid, value in body.decisions.items():
        if fid not in known:
            raise HTTPException(status_code=400,
                                detail=f"Unknown finding id '{fid}'.")
        if value not in DECISIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown decision '{value}' — expected one of "
                       f"{', '.join(DECISIONS)}.")
    stored = report.get("decisions") or {}
    # microseconds to match storage timestamps: stale is a lexicographic
    # decided_at > published_at comparison
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")
    for fid, value in body.decisions.items():
        # `open` is recorded too: reverting a decision is itself a review
        # event, and its timestamp is what makes a published copy stale
        stored[fid] = {"decision": value, "decided_at": now}
    report["decisions"] = stored
    storage.update_report(analysis_id, report)
    fresh = aggregate_findings(report)
    actionable = fresh["findings"] + fresh["unverified"]
    return {s: sum(1 for f in actionable if f["decision"] == s)
            for s in DECISIONS}


@app.post("/analyses/{analysis_id}/brief")
async def set_brief(analysis_id: str, request: Request) -> dict:
    """Step 1 of the storytelling flow: store the intake answers and return
    the proposed metric plan (Step 2), with honest per-question statuses."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    if not report.get("story"):
        raise HTTPException(
            status_code=422,
            detail="This analysis has no storytelling table — the brief flow "
                   "needs at least one table with numeric measures.")
    body = await request.json()
    brief = {
        "role": str(body.get("role") or "")[:80],
        "goals": [str(g)[:160] for g in (body.get("goals") or [])][:5],
        "questions": [str(q)[:200] for q in (body.get("questions") or [])][:8],
        "cadence": str(body.get("cadence") or "")[:30],
        "lang": "fr" if body.get("lang") == "fr" else "en",
    }
    if not brief["questions"]:
        raise HTTPException(status_code=400,
                            detail="A brief needs at least one question.")
    plan = plan_from_brief(brief, report.get("stories") or [report["story"]])
    report["brief"] = brief
    report["plan"] = plan
    storage.update_report(analysis_id, report)
    return {"brief": brief, "plan": plan}


@app.post("/analyses/{analysis_id}/plan")
async def approve_plan(analysis_id: str, request: Request) -> dict:
    """Approve (a subset of) the proposed plan: the metric ids the story
    dashboard will render."""
    report = storage.get_report(analysis_id)
    if report is None or not report.get("plan"):
        raise HTTPException(status_code=404,
                            detail="No plan to approve — set the brief first.")
    body = await request.json()
    stories = report.get("stories") or [report["story"]]
    valid = {f"s{si}:{m['id']}" for si, st in enumerate(stories)
             for m in st.get("metrics", [])}
    seen: set = set()
    approved = [i for i in (body.get("approved") or [])
                if i in valid and not (i in seen or seen.add(i))]
    report["plan"]["approved"] = approved
    storage.update_report(analysis_id, report)
    return {"approved": approved}


@app.get("/analyses/{analysis_id}/brief-suggestion")
def brief_suggestion(analysis_id: str, lang: str = "en") -> dict:
    """Intake helpers: the most recent brief from the same client workspace
    ('same brief as last month?') plus recommended goals & questions derived
    from what this workbook's stories can actually answer. Honest empties
    when there is nothing to suggest."""
    metas = storage.list_analyses()
    me = next((m for m in metas if m["id"] == analysis_id), None)
    if me is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")

    report = storage.get_report(analysis_id) or {}
    stories = report.get("stories") or \
        ([report["story"]] if report.get("story") else [])
    ideas = suggest_brief(stories, "fr" if lang == "fr" else "en")

    prior, prior_from = None, None
    if me.get("client"):
        for m in metas:                      # newest first already
            if m["id"] == analysis_id or m.get("client") != me["client"]:
                continue
            rep = storage.get_report(m["id"])
            if rep and rep.get("brief"):
                prior, prior_from = rep["brief"], m["filename"]
                break
    return {"brief": prior, "from": prior_from, "ideas": ideas}


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


@app.get("/analyses/{analysis_id}/mapping")
def get_mapping(analysis_id: str) -> dict:
    """The stored mapping (if any) plus the address space a mapping may
    point at: every series of every year-axis chart in this workbook."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    return {
        "mapping": report.get("mapping"),
        "available": [{"sheet": a["sheet"], "label": a["label"]}
                      for a in year_series(report)],
        "available_monthly": [{"sheet": a["sheet"], "label": a["label"]}
                              for a in month_series(report)],
        "roles": list(MAPPABLE_ROLES),
    }


@app.post("/analyses/{analysis_id}/mapping")
async def set_mapping(analysis_id: str, request: Request) -> dict:
    """Pin role -> series ({"mapping": {"revenue": {"label": ..., "sheet":
    ...}}}). Gated: a mapping the file's own identities contradict is
    refused (422) — a wrong mapping is a confident wrong dashboard."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    body = await request.json()
    mapping = body.get("mapping") or {}
    monthly = body.get("monthly") or {}
    if not (isinstance(mapping, dict) and isinstance(monthly, dict)) \
            or not (mapping or monthly):
        raise HTTPException(status_code=422, detail="Empty mapping.")

    resolved_y = resolved_m = None
    if mapping:
        resolved_y, errors = resolve(mapping, report)
        if resolved_y is None:
            raise HTTPException(status_code=422, detail="; ".join(errors))
    if monthly:
        resolved_m, errors = resolve(monthly, report, grain="monthly")
        if resolved_m is None:
            raise HTTPException(status_code=422, detail="; ".join(errors))

    # one gate over BOTH grains: any contradicted identity refuses the lot
    checks = []
    if resolved_y:
        checks += reconcile(resolved_y)["checks"]
    if resolved_m:
        checks += [{**c, "detail": f"monthly: {c['detail']}"}
                   for c in reconcile(resolved_m)["checks"]]
    ran = [c for c in checks if c["ok"] is not None]
    rec = {"checks": checks,
           "ok": all(c["ok"] for c in ran) if ran else None}
    if rec["ok"] is False:
        raise HTTPException(status_code=422, detail={
            "message": "mapping contradicts the data", "reconciliation": rec})

    if resolved_y:
        report["model"] = build_mapped_model(report, resolved_y)
    if resolved_m:
        mo, mgaps = build_mapped_monthly(report, resolved_m)
        model = report.get("model")
        if not model:            # nothing role-tagged and no year pins:
            model = {"periods": [], "source_sheet": None, "metrics": {},
                     "derived": {}, "breakdowns": [],
                     "scenario_ready": False}
        model["monthly"] = mo
        if mgaps:
            seen = {(g["metric"], g.get("grain"))
                    for g in model.get("gaps") or []}
            model.setdefault("gaps", []).extend(
                g for g in mgaps if (g["metric"], g.get("grain")) not in seen)
        report["model"] = model
    attach_plan_progress(report.get("model"), report.get("journal"),
                         plan_charts_from_report(report))
    report["mapping"] = {
        "roles": mapping,
        "monthly": monthly,
        "provenance": {"kind": "manual",
                       "at": _dt.datetime.now(_dt.timezone.utc)
                             .isoformat(timespec="seconds")},
        "reconciliation": rec,
    }
    storage.update_report(analysis_id, report)
    return {"model": report["model"], "mapping": report["mapping"],
            "reconciliation": rec}


@app.delete("/analyses/{analysis_id}/mapping")
def clear_mapping(analysis_id: str) -> dict:
    """Drop the mapping and fall back to the label heuristics."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    report.pop("mapping", None)
    report["model"] = build_model(AnalyzeResponse(**report).sheets)
    attach_plan_progress(report.get("model"), report.get("journal"),
                         plan_charts_from_report(report))
    storage.update_report(analysis_id, report)
    return {"cleared": True, "model": report["model"]}


@app.post("/analyses/{analysis_id}/publish")
def publish_analysis(analysis_id: str) -> dict:
    """Freeze the exec-only snapshot behind the public token.

    Gated: publishing with findings still `open` is refused — the product
    will not let unreviewed numbers circulate. Republish bumps the version;
    the token (and therefore the client's link) never changes."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    n_open = count_open(report)
    if n_open:
        raise HTTPException(
            status_code=409,
            detail=_bi(
                f"{n_open} finding(s) still open — decide each one before "
                "publishing.",
                f"{n_open} anomalie(s) en attente — tranchez chacune avant "
                "de publier."))
    info = storage.publish(analysis_id, build_exec_snapshot(report))
    label = storage.client_label(analysis_id)
    if label:                     # labeled work lands in the client hub too
        storage.record_dashboard_deliverable(
            analysis_id, label,
            title=_display_title(report.get("filename")) or analysis_id,
            version=info["version"], published_at=info["published_at"],
            status=_deliverable_status(report))
    return {**info, "url": f"/published/{info['token']}"}


def _display_title(filename: Optional[str]) -> str:
    """A filename is not client-facing language: strip the extension, turn
    separators into spaces — 'PVAK_T1_2026.xlsx' reads 'PVAK T1 2026'."""
    stem = (filename or "").rsplit(".", 1)[0]
    return re.sub(r"[_\-]+", " ", stem).strip()


@app.get("/analyses/{analysis_id}/publish")
def publish_status(analysis_id: str) -> dict:
    """Publish state for the workbook panel: version, when, opens, stale."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    info = storage.published_info(analysis_id)
    if info is None:
        return {"published": False}
    return {"published": True, **info,
            "url": f"/published/{info['token']}",
            "stale": storage.is_stale(report, info["published_at"])}


@app.delete("/analyses/{analysis_id}/publish")
def revoke_publish(analysis_id: str) -> dict:
    """Drop the published snapshot; the public link dies immediately."""
    return {"revoked": storage.revoke_publish(analysis_id)}


@app.get("/published/{token}")
def published_snapshot(token: str) -> dict:
    """PUBLIC. Serves the frozen exec-only snapshot VERBATIM — this handler
    never reads the full report, so analyst-only material cannot leak
    through it. Counts each open."""
    snap = storage.open_published(token)
    if snap is None:
        raise HTTPException(status_code=404,
                            detail=_bi("This link is no longer active.",
                                       "Ce lien n'est plus actif."))
    return snap


@app.get("/published/{token}/page", include_in_schema=False)
def published_page(token: str) -> FileResponse:
    """PUBLIC. The executive page shell; it fetches the snapshot above."""
    if not storage.published_token_exists(token):
        raise HTTPException(status_code=404,
                            detail=_bi("This link is no longer active.",
                                       "Ce lien n'est plus actif."))
    return FileResponse(_INDEX, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


# --- client report portals -------------------------------------------------
# Enabling/revoking a portal is analyst work (gated under /clients). Reading
# one is public: the unguessable token is the credential, and the payload is
# assembled only from frozen exec snapshots.

@app.post("/clients/{client}/portal")
def enable_portal(client: str) -> dict:
    """Mint (or return) the client's shareable portal link. The token is
    stable, so a client's link never changes once shared."""
    info = storage.ensure_portal(client)
    if info is None:
        raise HTTPException(status_code=422, detail="Client name is required.")
    return {**info, "url": f"/portal/{info['token']}"}


@app.get("/clients/{client}/portal")
def portal_status(client: str) -> dict:
    """Portal state for the client group header: enabled + shareable link."""
    info = storage.portal_by_client(client)
    if info is None:
        return {"enabled": False}
    return {"enabled": True, **info, "url": f"/portal/{info['token']}"}


@app.delete("/clients/{client}/portal")
def disable_portal(client: str) -> dict:
    """Drop the client's portal; the shared link dies immediately."""
    return {"revoked": storage.revoke_portal(client)}


# --- the client hub (authed portal) -----------------------------------------
# Literal /portal/* routes are registered BEFORE /portal/{token} below, so
# Starlette matches them first; real portal tokens (22 url-safe chars) can
# never collide with these names. Minting/revoking users is analyst work and
# stays behind the password; everything under /portal/ passes the analyst
# gate and is guarded by the client-session cookie instead.

def _client_identity(request: Request) -> dict:
    """The identity behind the client-session cookie. client_id is ALWAYS
    derived here, server-side — it is never a request parameter."""
    uid = auth.read_client_token(
        request.cookies.get(auth.CLIENT_COOKIE), "session",
        storage.app_secret(), auth.CLIENT_SESSION_MAX_AGE)
    if uid is None:
        raise HTTPException(status_code=401, detail="Sign-in required.")
    user = storage.client_user(uid)
    if user is None:                      # revoked while the session lived
        raise HTTPException(status_code=403,
                            detail="This access has been revoked.")
    return user


def _deliverable_status(report: dict) -> str | None:
    """The rollup chip frozen onto a deliverable at publish time. None when
    no relations were checked — we don't claim reconciliation that never
    ran."""
    agg = aggregate_findings(report)
    n_relations = (agg["n_verified_relations"] + agg["n_mismatched_relations"]
                   + agg["n_unverified_relations"])
    if n_relations == 0:
        return None
    if any((d or {}).get("decision") == "flagged"
           for d in (report.get("decisions") or {}).values()):
        return "flagged"
    return "reconciled"


@app.get("/clients/{client}/deliverables")
def client_deliverables_admin(client: str) -> list[dict]:
    """Operator: what this client's hub holds, each with a preview URL —
    the analyst must be able to SEE what was turned in without minting
    themselves a client login. File previews ride the same signed-token
    serving path the portal uses; dashboards link their published page."""
    rows = storage.list_deliverables_admin(client)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"No client '{client}'.")
    out = []
    for d in rows:
        url = None
        if d["kind"] == "file":
            token = auth.make_expiring_token("file", d["id"],
                                             storage.app_secret(),
                                             FILE_URL_TTL)
            url = f"/portal/files/{token}"
        else:
            pub = storage.published_info(d["source_ref"])
            if pub:
                url = f"/published/{pub['token']}/page"
        out.append({"id": d["id"], "title": d["title"], "kind": d["kind"],
                    "group": d["grp"], "version": d["version"],
                    "published_at": d["published_at"], "status": d["status"],
                    "url": url})
    return out


@app.post("/clients/{client}/analyst-access")
async def set_analyst_access(client: str, request: Request) -> dict:
    """Operator: open or close the analyst gate for one client. The exec
    view is the product; analyst depth (live audit detail on their
    deliverables) opens only when the client's own analysts need it."""
    body = await request.json()
    result = storage.set_client_analyst_access(client,
                                               bool(body.get("enabled")))
    if result is None:
        raise HTTPException(status_code=404, detail=f"No client '{client}'.")
    return {"client": client, "analyst_access": result}


@app.post("/clients/{client}/users")
async def add_portal_user(client: str, request: Request) -> dict:
    """Mint a login identity + its signed link ({"email": ...}). The analyst
    sends the link themselves (WhatsApp, email) — no hosted email flow."""
    body = await request.json()
    user = storage.add_client_user(client, body.get("email") or "")
    if user is None:
        raise HTTPException(
            status_code=422,
            detail="A valid email that isn't already on another client is "
                   "required.")
    token = auth.make_client_token("link", user["id"], storage.app_secret())
    return {"id": user["id"], "email": user["email"],
            "login_url": f"/portal/login/{token}"}


@app.get("/clients/{client}/users")
def portal_users(client: str) -> list[dict]:
    return storage.list_client_users(client)


@app.get("/clients")
def clients_index() -> list[dict]:
    """Every client with contact + deliverable counts, for the access panel."""
    return storage.list_clients()


def _client_contact_or_404(client: str, user_id: str) -> dict:
    """The contact row, 404 unless it belongs to this exact client — no
    cross-client link minting, ever."""
    user = storage.client_user(user_id)
    if user is None or user["name"] != (client or "").strip():
        raise HTTPException(status_code=404,
                            detail="No such contact for this client.")
    return user


@app.post("/clients/{client}/users/{user_id}/link")
def remint_login_link(client: str, user_id: str) -> dict:
    """A fresh signed login link for an EXISTING contact — creation is the
    only other moment a link exists."""
    user = _client_contact_or_404(client, user_id)
    token = auth.make_client_token("link", user_id, storage.app_secret())
    return {"id": user_id, "email": user["email"],
            "login_url": f"/portal/login/{token}"}


# invite email copy lives server-side: the email is a deliverable of the
# backend, not the SPA — FR first, like everything client-facing at PVAK
_INVITE_MAIL = {
    "en": {
        "subject": lambda c: f"Your {c} workspace at Lumnia — sign-in link",
        "body": lambda c, url: (
            f"Hello,\n\n"
            f"Here is your personal sign-in link for the {c} workspace at "
            f"Lumnia:\n\n{url}\n\n"
            f"The link is valid for 6 months and opens your read-only hub — "
            f"every deliverable we turn in for you, versioned.\n\n"
            f"If you weren't expecting this, you can ignore this email.\n"),
    },
    "fr": {
        "subject": lambda c: f"Votre espace {c} chez Lumnia — lien de connexion",
        "body": lambda c, url: (
            f"Bonjour,\n\n"
            f"Voici votre lien de connexion personnel à l'espace {c} chez "
            f"Lumnia :\n\n{url}\n\n"
            f"Ce lien est valable 6 mois et ouvre votre hub en lecture "
            f"seule — tous les livrables que nous vous remettons, "
            f"versionnés.\n\n"
            f"Si vous n'attendiez pas ce message, vous pouvez l'ignorer.\n"),
    },
}


@app.post("/clients/{client}/users/{user_id}/invite")
def email_invite(client: str, user_id: str, request: Request,
                 lang: str = "en") -> dict:
    """Mint a fresh sign-in link and EMAIL it to the contact. 503 when SMTP
    is unconfigured — the copy-link path in the workbench always works
    without it. SMTP failures surface as 502; nothing pretends to send."""
    if mailer.smtp_config() is None:
        raise HTTPException(
            status_code=503,
            detail="SMTP is not configured (set SMTP_HOST) — copy the "
                   "sign-in link instead.")
    user = _client_contact_or_404(client, user_id)
    token = auth.make_client_token("link", user_id, storage.app_secret())
    url = f"{str(request.base_url).rstrip('/')}/portal/login/{token}"
    copy = _INVITE_MAIL["fr" if lang == "fr" else "en"]
    try:
        mailer.send_invite(user["email"], copy["subject"](user["name"]),
                           copy["body"](user["name"], url))
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Sending failed: {exc}")
    return {"sent": True, "email": user["email"]}


# a signed file URL outlives its purpose quickly: long enough for a slow
# connection in Kinshasa, short enough that a forwarded link goes stale
FILE_URL_TTL = 15 * 60


@app.post("/analyses/{analysis_id}/brief-report")
def make_brief_report(analysis_id: str, lang: str = "en") -> dict:
    """Generate the written Analysis Brief and deliver it to the client hub
    as a versioned file deliverable. The pipeline supplies every figure
    (facts include monthly actuals + the vs-budget comparison); Claude only
    phrases the five fixed sections. Honest walls: 503 without a key, 409
    without a client to deliver to, 502 when the model's output is unusable."""
    if not narrative.available():
        raise HTTPException(
            status_code=503,
            detail=_bi(
                "AI narrative is not configured on this server.",
                "La rédaction IA n'est pas configurée sur ce serveur.")
            + " (ANTHROPIC_API_KEY)")
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    label = storage.client_label(analysis_id)
    if not label:
        raise HTTPException(
            status_code=409,
            detail=_bi(
                "Assign this analysis to a client first — the brief goes "
                "to the client's hub.",
                "Rattachez d'abord cette analyse à un client — le brief "
                "arrive dans son espace."))
    agg = aggregate_findings(report)
    facts = brief.build_brief_facts(report, agg)
    try:
        phrased = narrative.generate_brief(facts, brief.SECTION_KEYS, lang)
    except narrative.NarrativeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    html = brief.render_brief_html(report, phrased, agg, label, lang)
    stem = _display_title(report.get("filename")) or analysis_id
    d = storage.add_file_deliverable(
        label, f"analysis-brief-{lang}.html", html.encode("utf-8"),
        title=f"Analysis brief — {stem} ({lang.upper()})")
    if d is None:                      # label vanished mid-flight
        raise HTTPException(status_code=409, detail="Client no longer exists.")
    return {**d, "lang": lang}


@app.post("/analyses/{analysis_id}/compose-report")
def compose_report(analysis_id: str, req: ComposeReportRequest) -> dict:
    """Assemble the composed operations report from pipeline-computed blocks
    and deliver it to the client hub as a versioned file deliverable.
    Deterministic end to end — no AI, no scripts, charts as inline SVG.
    Gated like publish (open findings refuse) and like the brief (409
    without a client). Blocks the data cannot support are skipped and
    declared; requesting ONLY unsupported blocks is a 422, not an empty
    deliverable."""
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    n_open = count_open(report)
    if n_open:
        raise HTTPException(
            status_code=409,
            detail=_bi(
                f"{n_open} finding(s) still open — decide each one before "
                "delivering a report.",
                f"{n_open} anomalie(s) en attente — tranchez chacune avant "
                "de livrer un rapport."))
    label = storage.client_label(analysis_id)
    if not label:
        raise HTTPException(
            status_code=409,
            detail=_bi(
                "Assign this analysis to a client first — the report goes "
                "to the client's hub.",
                "Rattachez d'abord cette analyse à un client — le rapport "
                "arrive dans son espace."))
    unknown = [b for b in req.blocks if b not in compose.BLOCKS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown block(s): {', '.join(sorted(unknown))}. "
                   f"Valid: {', '.join(compose.BLOCKS)}.")
    avail = compose.available_blocks(report)
    wanted = req.blocks or list(compose.BLOCKS)
    chosen = [b for b in compose.BLOCKS if b in wanted and b in avail]
    skipped = [b for b in wanted if b not in avail]
    if not chosen:
        raise HTTPException(
            status_code=422,
            detail=_bi(
                "None of the requested blocks are computable from this "
                "workbook — nothing honest to deliver.",
                "Aucun des blocs demandés n'est calculable depuis ce "
                "classeur — rien d'honnête à livrer.")
            + f" ({', '.join(avail) or '—'})")
    lang = req.lang if req.lang in ("en", "fr") else "en"
    agg = aggregate_findings(report)
    html = compose.render_report_html(report, agg, label, lang, chosen)
    stem = _display_title(report.get("filename")) or analysis_id
    d = storage.add_file_deliverable(
        label, f"operations-report-{lang}.html", html.encode("utf-8"),
        title=f"{compose._STR[lang]['kicker']} — {stem} ({lang.upper()})")
    if d is None:                      # label vanished mid-flight
        raise HTTPException(status_code=409, detail="Client no longer exists.")
    return {**d, "blocks": chosen, "skipped": skipped, "lang": lang}


@app.post("/clients/{client}/files")
async def add_client_file(client: str, file: UploadFile = File(...),
                          group: str = Form(""), title: str = Form("")) -> dict:
    """Analyst-gated: turn in a file (.xlsx/.pdf/...) for a client. Same
    title re-delivered bumps the version on the same deliverable."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=_bi(
                f"'{file.filename}' exceeds the "
                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                f"« {file.filename} » dépasse la limite de "
                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} Mo."))
    d = storage.add_file_deliverable(client, file.filename or "", content,
                                     group=group, title=title)
    if d is None:
        raise HTTPException(status_code=422,
                            detail="A client name and a filename are required.")
    return d


@app.get("/portal/files/{token}", include_in_schema=False)
def portal_file(token: str) -> FileResponse:
    """PUBLIC. Serve file bytes behind a short-TTL signed token. The token is
    minted only after an ownership check in the detail endpoint, and its
    expiry is inside the MAC — an expired or tampered link is a 404."""
    did = auth.read_expiring_token(token, "file", storage.app_secret())
    if did is None:
        raise HTTPException(status_code=404,
                            detail=_bi("This link is no longer active.",
                                       "Ce lien n'est plus actif."))
    found = storage.file_deliverable_path(did)
    if found is None:
        raise HTTPException(status_code=404,
                            detail=_bi("This link is no longer active.",
                                       "Ce lien n'est plus actif."))
    path, name = found
    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
    inline = media in ("application/pdf", "text/html")
    headers = {
        "Content-Disposition":
            f'{"inline" if inline else "attachment"}; filename="{name}"',
        "Cache-Control": "no-store"}
    if media == "text/html":
        # interactive dashboards may run their scripts, but in an OPAQUE
        # ORIGIN (CSP sandbox): no cookies, no session, no same-origin API
        # reach — nothing served here can act on the client's session
        headers["Content-Security-Policy"] = "sandbox allow-scripts"
    return FileResponse(path, media_type=media, headers=headers)


@app.delete("/clients/{client}/users/{user_id}")
def revoke_portal_user(client: str, user_id: str) -> dict:
    """Drop the identity: the login link AND any live session die at once."""
    return {"revoked": storage.remove_client_user(user_id)}


# the one neutral answer for /portal/request-link — identical for known,
# unknown, and throttled addresses, and honest for all three: a recognized
# address is mailed (or queued when SMTP is off), an unknown one waits for
# the analyst. EN leads, FR rides.
_REQUEST_LINK_OK = ("Thank you. If your address is recognized your link "
                    "arrives by email; otherwise your request has been "
                    "passed to your analyst. · Merci. Si votre adresse est "
                    "reconnue, votre lien arrive par email ; sinon votre "
                    "demande a été transmise à votre analyste.")
_REQUEST_LINK_BAD = ("Enter a valid email address. · "
                     "Entrez une adresse email valide.")


@app.get("/portal/signin", include_in_schema=False)
def portal_signin_page() -> HTMLResponse:
    """PUBLIC. The client sign-in page: email in, link by email out."""
    return HTMLResponse(auth.PORTAL_SIGNIN_HTML.replace("__MSG__", ""))


@app.post("/portal/request-link", include_in_schema=False)
def portal_request_link(request: Request, email: str = Form(...)):
    """PUBLIC. A registered contact gets a fresh sign-in link by email; an
    unknown address becomes an access request on the operator's desk. The
    response NEVER distinguishes the cases — no way to probe which emails
    are Lumnia clients — and the link itself only ever travels by email."""
    addr = (email or "").strip().lower()
    page = lambda msg: HTMLResponse(  # noqa: E731 — tiny local shorthand
        auth.PORTAL_SIGNIN_HTML.replace("__MSG__", msg))
    if not addr or "@" not in addr:
        return page(_REQUEST_LINK_BAD)
    if not storage.link_request_allowed(addr):
        return page(_REQUEST_LINK_OK)          # throttled, same face
    user = storage.client_user_by_email(addr)
    if user is not None and mailer.smtp_config() is not None:
        token = auth.make_client_token("link", user["id"],
                                       storage.app_secret())
        url = f"{str(request.base_url).rstrip('/')}/portal/login/{token}"
        copy = _INVITE_MAIL["fr"]              # anchor clientele is FR
        try:
            mailer.send_invite(addr, copy["subject"](user["name"]),
                               copy["body"](user["name"], url))
        except Exception:
            # deliberate: a send failure must not change the public answer
            # (that would leak which addresses are real); it is logged for
            # the operator instead
            logging.exception("request-link send failed for %s", addr)
    else:
        # unknown address — or SMTP off, where even a known contact needs
        # the analyst to hand over the link — waits on the operator's desk
        storage.add_access_request(addr)
    return page(_REQUEST_LINK_OK)


@app.get("/access-requests")
def access_requests() -> list[dict]:
    """Operator: the pending public sign-in requests."""
    return storage.list_access_requests()


@app.post("/access-requests/{request_id}/approve")
async def approve_access_request(request_id: str, request: Request) -> dict:
    """Put the requested email on a client (operator's explicit choice),
    mint its sign-in link, email it when SMTP is up — and always hand the
    link back so the copy-paste path works without SMTP."""
    body = await request.json()
    reqs = {r["id"]: r for r in storage.list_access_requests()}
    if request_id not in reqs:
        raise HTTPException(status_code=404, detail="No such request.")
    email_addr = reqs[request_id]["email"]
    user = storage.add_client_user(body.get("client") or "", email_addr)
    if user is None:
        raise HTTPException(
            status_code=422,
            detail=_bi(
                "A client name is required, and the email must not already "
                "belong to another client.",
                "Un nom de client est requis, et l'email ne doit pas déjà "
                "appartenir à un autre client."))
    token = auth.make_client_token("link", user["id"], storage.app_secret())
    url = f"{str(request.base_url).rstrip('/')}/portal/login/{token}"
    emailed = False
    if mailer.smtp_config() is not None:
        client_name = (body.get("client") or "").strip()
        copy = _INVITE_MAIL["fr" if body.get("lang") == "fr" else "en"]
        try:
            mailer.send_invite(email_addr, copy["subject"](client_name),
                               copy["body"](client_name, url))
            emailed = True
        except Exception as exc:
            raise HTTPException(status_code=502,
                                detail=f"Sending failed: {exc}")
    storage.pop_access_request(request_id)
    return {"client": (body.get("client") or "").strip(),
            "email": email_addr, "emailed": emailed,
            "login_url": f"/portal/login/{token}"}


@app.delete("/access-requests/{request_id}")
def dismiss_access_request(request_id: str) -> dict:
    """Drop a pending request without creating anything."""
    return {"dismissed": storage.pop_access_request(request_id) is not None}


@app.get("/portal/login/{token}", include_in_schema=False)
def portal_login(token: str, request: Request):
    """PUBLIC. Exchange a signed login link for a client session cookie."""
    uid = auth.read_client_token(token, "link", storage.app_secret(),
                                 auth.LINK_MAX_AGE)
    if uid is None or storage.client_user(uid) is None:
        raise HTTPException(status_code=403,
                            detail="This sign-in link is no longer active.")
    resp = RedirectResponse("/portal/hub", status_code=303)
    resp.set_cookie(auth.CLIENT_COOKIE,
                    auth.make_client_token("session", uid,
                                           storage.app_secret()),
                    max_age=auth.CLIENT_SESSION_MAX_AGE, httponly=True,
                    samesite="lax", secure=_secure(request))
    return resp


@app.post("/portal/intake")
async def portal_intake(request: Request,
                        file: UploadFile = File(...)) -> dict:
    """CLIENT-SESSION. A client drops their own workbook: it runs the SAME
    pipeline as an analyst upload and lands in the operator workspace with
    the client pre-assigned — nothing published, nothing analytical returned.
    The client gets a receipt; the analyst gets the audit."""
    user = _client_identity(request)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400,
                            detail=_bi("Empty upload.", "Fichier vide."))
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=_bi(
                f"File is {len(content) / 1e6:.1f} MB; the limit is "
                f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB.",
                f"Le fichier fait {len(content) / 1e6:.1f} Mo ; la limite "
                f"est {MAX_UPLOAD_BYTES / 1e6:.0f} Mo."))
    existing = storage.find_by_content(content)
    if existing is not None:
        # same bytes, no duplicate row — but NEVER reassign an analysis a
        # different client already owns
        if storage.client_label(existing) is None:
            storage.set_client(existing, user["name"])
        return {"received": True, "filename": file.filename or ""}
    result = await run_in_threadpool(run_pipeline, content, file.filename or "")
    report = result.model_dump()
    _inherit_mapping(report)
    result = AnalyzeResponse(**report)
    aid = storage.save_analysis(result.filename, content, result.model_dump(),
                                origin="intake")
    storage.set_client(aid, user["name"])
    return {"received": True, "filename": result.filename}


@app.get("/portal/logout", include_in_schema=False)
def portal_logout() -> RedirectResponse:
    """CLIENT. Kill the session cookie on this device — shared phones and
    cybercafé machines are the anchor market's normal case."""
    resp = RedirectResponse("/portal/signin", status_code=303)
    resp.delete_cookie(auth.CLIENT_COOKIE)
    return resp


@app.get("/portal/hub", include_in_schema=False)
def portal_hub_page(request: Request) -> FileResponse:
    """The hub shell; the page boots from /portal/me. Session required."""
    _client_identity(request)
    return FileResponse(_INDEX, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


@app.get("/portal/me", response_model=PortalMe)
def portal_me(request: Request) -> PortalMe:
    user = _client_identity(request)
    return PortalMe(client=PortalClient(name=user["name"], slug=user["slug"]),
                    email=user["email"])


@app.get("/portal/deliverables", response_model=list[DeliverableMeta])
def portal_deliverables(request: Request) -> list[DeliverableMeta]:
    user = _client_identity(request)
    return [DeliverableMeta(id=d["id"], title=d["title"], kind=d["kind"],
                            group=d["grp"], version=d["version"],
                            published_at=d["published_at"],
                            status=d["status"])
            for d in storage.list_deliverables(user["client_id"])]


@app.get("/portal/deliverables/{deliverable_id}")
def portal_deliverable(deliverable_id: str, request: Request) -> dict:
    """One deliverable. Dashboards return the frozen exec-only snapshot —
    the same curated payload the public token link serves, no new render
    path. Cross-tenant ids 404: we don't confirm they exist.

    When the client's analyst gate is OPEN (off by default, operator's
    explicit choice), dashboards also carry ``audit_detail`` — the LIVE
    reconciliation audit of the source workbook, read-only, for the
    client's own analysts."""
    user = _client_identity(request)
    d = storage.get_deliverable(user["client_id"], deliverable_id,
                                with_source=True)
    if d is None or (d["kind"] == "dashboard" and d.get("snapshot") is None):
        raise HTTPException(status_code=404, detail="No such deliverable.")
    source_ref = d.pop("_source_ref", None)   # server-side only, never out
    if (d["kind"] == "dashboard" and source_ref
            and storage.client_analyst_access(user["client_id"])):
        report = storage.get_report(source_ref)
        if report is not None:
            agg = aggregate_findings(report)
            d["audit_detail"] = {
                k: agg.get(k) for k in
                ("n_verified_relations", "n_mismatched_relations",
                 "n_unverified_relations", "total_abs_delta",
                 "findings", "unverified")}
    if d["kind"] == "file":       # mint the signed URL only post-check
        token = auth.make_expiring_token("file", d["id"],
                                         storage.app_secret(), FILE_URL_TTL)
        found = storage.file_deliverable_path(d["id"])
        d["filename"] = found[1] if found else None
        d["signed_url"] = f"/portal/files/{token}"
        d["expires_at"] = int(_dt.datetime.now(_dt.timezone.utc).timestamp()
                              ) + FILE_URL_TTL
    return d


@app.get("/portal/{token}")
def portal_snapshot(token: str) -> dict:
    """PUBLIC. A client's currently-published dashboards, recomputed live from
    the frozen exec snapshots. This handler never reads a full report, so
    analyst-only material cannot leak through it, and an unpublished analysis
    is simply absent."""
    view = storage.open_portal(token)
    if view is None:
        raise HTTPException(status_code=404,
                            detail=_bi("This link is no longer active.",
                                       "Ce lien n'est plus actif."))
    return view


@app.get("/portal/{token}/page", include_in_schema=False)
def portal_page(token: str) -> FileResponse:
    """PUBLIC. The portal page shell; it fetches the listing above."""
    if not storage.portal_token_exists(token):
        raise HTTPException(status_code=404,
                            detail=_bi("This link is no longer active.",
                                       "Ce lien n'est plus actif."))
    return FileResponse(_INDEX, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


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
            detail=_bi(
                "AI narrative is not configured on this server.",
                "La rédaction IA n'est pas configurée sur ce serveur.")
            + " (ANTHROPIC_API_KEY)")
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
    old = storage.get_report(analysis_id) or {}
    result = run_pipeline(content, filename)
    result.id = analysis_id
    # the brief is the user's intent, not a computation — it survives reruns;
    # the plan is re-matched against the fresh story (approvals kept where
    # the metric still exists)
    if old.get("brief") and result.story:
        result.brief = old["brief"]
        result.plan = plan_from_brief(old["brief"],
                                      result.stories or [result.story])
        prev = (old.get("plan") or {}).get("approved")
        if prev is not None:
            valid = {f"s{si}:{m['id']}"
                     for si, st in enumerate(result.stories or [result.story])
                     for m in st.get("metrics", [])}
            result.plan["approved"] = [i for i in prev if i in valid]
    # decisions survive the rerun for findings whose stable id still exists;
    # decisions on vanished findings are dropped (same policy as approvals)
    if old.get("decisions"):
        fresh = aggregate_findings(result.model_dump())
        alive = {f["id"] for f in
                 fresh["findings"] + fresh["unverified"] + fresh["verified"]}
        kept = {fid: d for fid, d in old["decisions"].items() if fid in alive}
        result.decisions = kept or None
    # a pinned mapping is the analyst's call — it survives the rerun as long
    # as its series still resolve and the data does not contradict it,
    # both grains together (all-or-nothing, same gate as setting it)
    old_map = old.get("mapping") or {}
    if old_map.get("roles") or old_map.get("monthly"):
        rep = result.model_dump()
        resolved_y = resolved_m = None
        ok = True
        if old_map.get("roles"):
            resolved_y, _errors = resolve(old_map["roles"], rep)
            ok = ok and resolved_y is not None
        if old_map.get("monthly"):
            resolved_m, _errors = resolve(old_map["monthly"], rep,
                                          grain="monthly")
            ok = ok and resolved_m is not None
        if ok:
            checks = []
            if resolved_y:
                checks += reconcile(resolved_y)["checks"]
            if resolved_m:
                checks += [{**c, "detail": f"monthly: {c['detail']}"}
                           for c in reconcile(resolved_m)["checks"]]
            ran = [c for c in checks if c["ok"] is not None]
            rec = {"checks": checks,
                   "ok": all(c["ok"] for c in ran) if ran else None}
            if rec["ok"] is not False:
                model = build_mapped_model(rep, resolved_y) \
                    if resolved_y else result.model
                if resolved_m:
                    rep_now = dict(rep, model=model)
                    mo, _mgaps = build_mapped_monthly(rep_now, resolved_m)
                    model = model or {"periods": [], "source_sheet": None,
                                      "metrics": {}, "derived": {},
                                      "breakdowns": [],
                                      "scenario_ready": False}
                    model["monthly"] = mo
                attach_plan_progress(model, rep.get("journal"),
                                     plan_charts_from_report(rep))
                result.model = model
                result.mapping = {**old_map, "reconciliation": rec}
    storage.update_report(analysis_id, result.model_dump())
    return result


# Trace requests are small: a figure cites a handful of cells per period.
# The frontend chunks longer citation lists into several requests.
MAX_TRACE_REFS = 40
_A1_RX = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]{0,6})$")
_EXCERPT_PAD = 2          # rows of context above/below the cited cells
_EXCERPT_MAX_ROWS = 10
_EXCERPT_MAX_COLS = 12
# cell content is echoed verbatim up to this length — a crafted workbook can
# hold multi-MB text cells, and the modal shows a short prefix anyway
_MAX_CELL_CHARS = 300


def _parse_a1(ref: str) -> tuple:
    """'B3' -> (0-based row, 0-based col). ValueError on junk."""
    m = _A1_RX.match(ref)
    if not m:
        raise ValueError(ref)
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)) - 1, col - 1


def _clip(v):
    """Bound echoed cell content; the '…' marks the clip honestly."""
    if isinstance(v, str) and len(v) > _MAX_CELL_CHARS:
        return v[:_MAX_CELL_CHARS] + "…"
    return v


@app.get("/analyses/{analysis_id}/cells", response_model=CellTraceResponse)
def trace_cells(analysis_id: str, sheet: str, refs: str) -> CellTraceResponse:
    """Resolve A1 refs against the ORIGINAL uploaded bytes — the proof
    behind a dashboard figure. The workbook is re-read through the same
    ingest path the pipeline used; nothing is echoed from the stored
    report, so what this returns is what the file actually holds."""
    ref_list = [r.strip().upper() for r in refs.split(",") if r.strip()]
    if not ref_list:
        raise HTTPException(status_code=400, detail=_bi(
            "No cell refs given.", "Aucune référence de cellule fournie."))
    if len(ref_list) > MAX_TRACE_REFS:
        raise HTTPException(status_code=400, detail=_bi(
            f"Too many cell refs ({len(ref_list)}); the limit is "
            f"{MAX_TRACE_REFS}.",
            f"Trop de références ({len(ref_list)}) ; la limite est "
            f"{MAX_TRACE_REFS}."))
    try:
        coords = [_parse_a1(r) for r in ref_list]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_bi(
            f"Malformed cell ref '{exc.args[0]}'.",
            f"Référence de cellule invalide « {exc.args[0]} »."))

    stored = storage.get_content(analysis_id)
    if stored is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    filename, content = stored
    # DEBT: re-parses the whole workbook (all sheets) on every trace click.
    # Fine at demo scale; needs a per-analysis parsed-grid cache if books
    # grow or tracing ever becomes client-facing.
    try:
        sheets = read_upload(content, filename)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=_bi(
            f"Could not re-read '{filename}'",
            f"Impossible de relire « {filename} »") + f" — {exc}")
    if sheet not in sheets:
        raise HTTPException(status_code=404, detail=_bi(
            f"No sheet '{sheet}' in '{filename}'.",
            f"Aucune feuille « {sheet} » dans « {filename} »."))
    df = sheets[sheet]
    n_rows, n_cols = int(df.shape[0]), int(df.shape[1])
    for ref, (i, j) in zip(ref_list, coords):
        if i >= n_rows or j >= n_cols:
            raise HTTPException(status_code=400, detail=_bi(
                f"Cell {ref} is outside sheet '{sheet}' "
                f"({n_rows} rows x {n_cols} columns).",
                f"La cellule {ref} est hors de la feuille « {sheet} » "
                f"({n_rows} lignes x {n_cols} colonnes)."))

    cells = []
    for ref, (i, j) in zip(ref_list, coords):
        raw = df.iat[i, j]
        cells.append({"ref": ref, "raw": _clip(jsonify(raw)),
                      "value": _clip(jsonify(coerce_value(raw)))})

    # context slice: the cited rows padded, plus the columns around the
    # citations AND column A — the row label lives left and must survive a
    # wide monthly sheet (col_letters name each column, so a gap is honest)
    r_lo = max(0, min(i for i, _ in coords) - _EXCERPT_PAD)
    r_hi = min(n_rows - 1, max(i for i, _ in coords) + _EXCERPT_PAD)
    if r_hi - r_lo + 1 > _EXCERPT_MAX_ROWS:
        r_hi = r_lo + _EXCERPT_MAX_ROWS - 1
    c_lo = max(0, min(j for _, j in coords) - 1)
    c_hi = min(n_cols - 1, max(j for _, j in coords) + 1)
    xcols = sorted({0, *range(c_lo, c_hi + 1)})
    if len(xcols) > _EXCERPT_MAX_COLS:
        xcols = [0] + [c for c in xcols if c][-(_EXCERPT_MAX_COLS - 1):]
    excerpt = {
        "row_start": r_lo + 1,
        "col_letters": [_xl_col(j) for j in xcols],
        "rows": [[_clip(jsonify(df.iat[i, j])) for j in xcols]
                 for i in range(r_lo, r_hi + 1)],
    }
    return CellTraceResponse(id=analysis_id, filename=filename, sheet=sheet,
                             cells=cells, excerpt=excerpt)


@app.delete("/analyses/{analysis_id}", response_model=DeleteResponse)
def delete_analysis(analysis_id: str) -> DeleteResponse:
    if not storage.delete_analysis(analysis_id):
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    return DeleteResponse(id=analysis_id, deleted=True)
