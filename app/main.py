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

import mimetypes
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

import datetime as _dt

from . import auth, brief, narrative, storage
from .findings import DECISIONS, aggregate_findings, count_open
from .snapshot import build_exec_snapshot
from .models import (
    AnalysisMeta,
    AnalyzeResponse,
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
from .pipeline.model import build_model
from .pipeline.mapping import (MAPPABLE_ROLES, build_mapped_model, reconcile,
                               resolve, year_series)
from .pipeline.semantics import plan_from_brief, suggest_brief
from .pipeline.ingest import read_upload
from .pipeline.orient import orient_sheet
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
            detail=f"Could not read '{filename}': {exc}",
        )

    total_cells = sum(int(df.shape[0]) * int(df.shape[1])
                      for df in sheets.values())
    if total_cells > MAX_TOTAL_CELLS:
        raise HTTPException(
            status_code=422,
            detail=f"'{filename}' has {total_cells:,} cells across "
                   f"{len(sheets)} sheet(s); the limit is "
                   f"{MAX_TOTAL_CELLS:,}. Split the workbook or remove "
                   f"unused sheets.")

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
    return AnalyzeResponse(
        filename=filename or "upload",
        n_sheets=len(reports),
        sheets=reports,
        insights=generate_insights(eda_results) or None,
        model=build_model(reports),
        story=stories[0] if stories else None,
        stories=stories or None,
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

    # CPU-bound pandas work must not run on the event loop: inline it and
    # one big upload freezes every other request AND the /health check,
    # which lets Fly/Render restart the box mid-analysis.
    result = await run_in_threadpool(run_pipeline, content, file.filename or "")
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
    if not isinstance(mapping, dict) or not mapping:
        raise HTTPException(status_code=422, detail="Empty mapping.")

    resolved, errors = resolve(mapping, report)
    if resolved is None:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    rec = reconcile(resolved)
    if rec["ok"] is False:
        raise HTTPException(status_code=422, detail={
            "message": "mapping contradicts the data", "reconciliation": rec})

    report["model"] = build_mapped_model(report, resolved)
    report["mapping"] = {
        "roles": mapping,
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
            detail=f"{n_open} finding(s) still open — decide each one "
                   f"(approve or flag) before publishing.")
    info = storage.publish(analysis_id, build_exec_snapshot(report))
    label = storage.client_label(analysis_id)
    if label:                     # labeled work lands in the client hub too
        storage.record_dashboard_deliverable(
            analysis_id, label,
            title=(report.get("filename") or "").rsplit(".", 1)[0]
                  or analysis_id,
            version=info["version"], published_at=info["published_at"],
            status=_deliverable_status(report))
    return {**info, "url": f"/published/{info['token']}"}


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
                            detail="This link is no longer active.")
    return snap


@app.get("/published/{token}/page", include_in_schema=False)
def published_page(token: str) -> FileResponse:
    """PUBLIC. The executive page shell; it fetches the snapshot above."""
    if not storage.published_token_exists(token):
        raise HTTPException(status_code=404,
                            detail="This link is no longer active.")
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


@app.post("/clients/{client}/users/{user_id}/link")
def remint_login_link(client: str, user_id: str) -> dict:
    """A fresh signed login link for an EXISTING contact — creation is the
    only other moment a link exists. 404 unless the contact belongs to this
    exact client: no cross-client minting."""
    user = storage.client_user(user_id)
    if user is None or user["name"] != (client or "").strip():
        raise HTTPException(status_code=404,
                            detail="No such contact for this client.")
    token = auth.make_client_token("link", user_id, storage.app_secret())
    return {"id": user_id, "email": user["email"],
            "login_url": f"/portal/login/{token}"}


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
            detail="AI narrative is not configured on this server "
                   "(set the ANTHROPIC_API_KEY secret).")
    report = storage.get_report(analysis_id)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    label = storage.client_label(analysis_id)
    if not label:
        raise HTTPException(
            status_code=409,
            detail="Assign this analysis to a client first — the brief is "
                   "delivered to the client's hub.")
    agg = aggregate_findings(report)
    facts = brief.build_brief_facts(report, agg)
    try:
        phrased = narrative.generate_brief(facts, brief.SECTION_KEYS, lang)
    except narrative.NarrativeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    html = brief.render_brief_html(report, phrased, agg, label, lang)
    stem = (report.get("filename") or analysis_id).rsplit(".", 1)[0]
    d = storage.add_file_deliverable(
        label, f"analysis-brief-{lang}.html", html.encode("utf-8"),
        title=f"Analysis brief — {stem} ({lang.upper()})")
    if d is None:                      # label vanished mid-flight
        raise HTTPException(status_code=409, detail="Client no longer exists.")
    return {**d, "lang": lang}


@app.post("/clients/{client}/files")
async def add_client_file(client: str, file: UploadFile = File(...),
                          group: str = Form(""), title: str = Form("")) -> dict:
    """Analyst-gated: turn in a file (.xlsx/.pdf/...) for a client. Same
    title re-delivered bumps the version on the same deliverable."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"'{file.filename}' exceeds the "
                   f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
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
                            detail="This link is no longer active.")
    found = storage.file_deliverable_path(did)
    if found is None:
        raise HTTPException(status_code=404,
                            detail="This link is no longer active.")
    path, name = found
    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
    inline = media in ("application/pdf", "text/html")
    headers = {
        "Content-Disposition":
            f'{"inline" if inline else "attachment"}; filename="{name}"',
        "Cache-Control": "no-store"}
    if media == "text/html":
        # readable in place, but script-dead: generated briefs carry no JS,
        # and nothing served here may ever act on the client's session
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(path, media_type=media, headers=headers)


@app.delete("/clients/{client}/users/{user_id}")
def revoke_portal_user(client: str, user_id: str) -> dict:
    """Drop the identity: the login link AND any live session die at once."""
    return {"revoked": storage.remove_client_user(user_id)}


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
    path. Cross-tenant ids 404: we don't confirm they exist."""
    user = _client_identity(request)
    d = storage.get_deliverable(user["client_id"], deliverable_id)
    if d is None or (d["kind"] == "dashboard" and d.get("snapshot") is None):
        raise HTTPException(status_code=404, detail="No such deliverable.")
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
                            detail="This link is no longer active.")
    return view


@app.get("/portal/{token}/page", include_in_schema=False)
def portal_page(token: str) -> FileResponse:
    """PUBLIC. The portal page shell; it fetches the listing above."""
    if not storage.portal_token_exists(token):
        raise HTTPException(status_code=404,
                            detail="This link is no longer active.")
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
    # as its series still resolve and the data does not contradict it
    old_map = old.get("mapping") or {}
    if old_map.get("roles"):
        rep = result.model_dump()
        resolved, _errors = resolve(old_map["roles"], rep)
        if resolved:
            rec = reconcile(resolved)
            if rec["ok"] is not False:
                result.model = build_mapped_model(rep, resolved)
                result.mapping = {**old_map, "reconciliation": rec}
    storage.update_report(analysis_id, result.model_dump())
    return result


@app.delete("/analyses/{analysis_id}", response_model=DeleteResponse)
def delete_analysis(analysis_id: str) -> DeleteResponse:
    if not storage.delete_analysis(analysis_id):
        raise HTTPException(status_code=404,
                            detail=f"No analysis '{analysis_id}'.")
    return DeleteResponse(id=analysis_id, deleted=True)
