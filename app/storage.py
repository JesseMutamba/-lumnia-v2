"""Step 5: persistence for analyses — SQLite, stdlib only.

Design decisions, deliberately minimal:

* One table. The full report is stored as a JSON blob (it is already the API
  response shape); no relational modelling of sheets/columns until something
  actually queries them.
* The **original upload bytes are kept** next to the report. The pipeline
  improves step by step, so any stored file can be re-analyzed later
  (``POST /analyses/{id}/rerun``) without asking the client to re-upload.
* DB path comes from ``LUMNIA_DB`` (default ``data/lumnia.db``), resolved per
  call so tests can point it at a temp file. Connections are per-operation:
  correctness over micro-performance at this stage.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    reran_at    TEXT,
    size_bytes  INTEGER NOT NULL,
    sha256      TEXT,
    content     BLOB NOT NULL,
    report      TEXT NOT NULL
)
"""

# Read-only share links: an unguessable token maps to one analysis. One token
# per analysis (creating again returns the same one); deleting the analysis
# deletes its token, revoking the link.
_SCHEMA_SHARES = """
CREATE TABLE IF NOT EXISTS shares (
    token       TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""

# Publish lifecycle: a frozen EXEC-ONLY snapshot behind a stable public
# token. The snapshot is what the public endpoint serves — verbatim — so
# analyst-only material can never leak through that path. `shares` above is
# the superseded mechanism; rows are dropped with their analysis.
_SCHEMA_PUBLISHED = """
CREATE TABLE IF NOT EXISTS published (
    analysis_id  TEXT PRIMARY KEY,
    version      INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    token        TEXT NOT NULL,
    snapshot     TEXT NOT NULL,
    opened_count INTEGER NOT NULL DEFAULT 0
)
"""


def _db_path() -> Path:
    return Path(os.environ.get("LUMNIA_DB", "data/lumnia.db"))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    con.execute(_SCHEMA_SHARES)
    con.execute(_SCHEMA_PUBLISHED)
    for migration in (                # migrate DBs created before the column
        "ALTER TABLE analyses ADD COLUMN sha256 TEXT",
        "ALTER TABLE analyses ADD COLUMN client TEXT",
        "ALTER TABLE analyses ADD COLUMN open_findings INTEGER",
        "ALTER TABLE analyses ADD COLUMN n_sheets INTEGER",
        "ALTER TABLE analyses ADD COLUMN checks_ok INTEGER",
        "ALTER TABLE analyses ADD COLUMN checks_total INTEGER",
        "ALTER TABLE analyses ADD COLUMN last_decided_at TEXT",
    ):
        try:
            con.execute(migration)
        except sqlite3.OperationalError:
            pass
    return con


def _now() -> str:
    # microseconds: publish/decision ordering is compared lexicographically,
    # and a decide-then-publish in the same second must not read as stale
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def find_by_content(content: bytes) -> Optional[str]:
    """Id of an existing analysis of these exact bytes, if any."""
    digest = hashlib.sha256(content).hexdigest()
    with _connect() as con:
        row = con.execute("SELECT id FROM analyses WHERE sha256 = ?",
                          (digest,)).fetchone()
    return row["id"] if row else None


def _rollup(report: Dict[str, Any]) -> Dict[str, Any]:
    """The denormalized status columns, computed once per write so the Home
    listing and stats never have to parse report blobs."""
    # local import: findings.py is pure and imports nothing from storage
    from .findings import aggregate_findings
    agg = aggregate_findings(report)
    decided = [(d or {}).get("decided_at", "")
               for d in (report.get("decisions") or {}).values()]
    return {
        "open_findings": sum(
            1 for f in agg["findings"] + agg["unverified"]
            if f["decision"] == "open"),
        "n_sheets": report.get("n_sheets", 0),
        "checks_ok": agg["n_verified_relations"],
        "checks_total": (agg["n_verified_relations"]
                         + agg["n_mismatched_relations"]),
        "last_decided_at": max(decided) if decided else None,
    }


def save_analysis(filename: str, content: bytes, report: Dict[str, Any]) -> str:
    """Persist a new analysis; returns its id (also stamped into the report)."""
    analysis_id = uuid.uuid4().hex[:12]
    report = {**report, "id": analysis_id}
    roll = _rollup(report)
    with _connect() as con:
        con.execute(
            "INSERT INTO analyses (id, filename, uploaded_at, size_bytes, sha256, content, report, "
            "open_findings, n_sheets, checks_ok, checks_total, last_decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (analysis_id, filename, _now(), len(content),
             hashlib.sha256(content).hexdigest(), content,
             json.dumps(report, ensure_ascii=False),
             roll["open_findings"], roll["n_sheets"], roll["checks_ok"],
             roll["checks_total"], roll["last_decided_at"]),
        )
    return analysis_id


def list_analyses() -> List[Dict[str, Any]]:
    """Newest-first metadata for every stored analysis — served from the
    denormalized status columns. Report blobs are parsed only as a one-time
    backfill for rows written before the columns existed."""
    with _connect() as con:
        rows = con.execute(
            "SELECT a.id, a.filename, a.uploaded_at, a.reran_at, a.size_bytes, "
            "a.client, a.open_findings, a.n_sheets, a.checks_ok, "
            "a.checks_total, a.last_decided_at, "
            "p.version AS pub_version, p.published_at AS pub_at "
            "FROM analyses a LEFT JOIN published p ON p.analysis_id = a.id "
            "ORDER BY a.uploaded_at DESC, a.id"
        ).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            if r["n_sheets"] is None:      # legacy row: backfill once
                blob = con.execute("SELECT report FROM analyses WHERE id = ?",
                                   (r["id"],)).fetchone()["report"]
                roll = _rollup(json.loads(blob))
                con.execute(
                    "UPDATE analyses SET open_findings = ?, n_sheets = ?, "
                    "checks_ok = ?, checks_total = ?, last_decided_at = ? "
                    "WHERE id = ?",
                    (roll["open_findings"], roll["n_sheets"],
                     roll["checks_ok"], roll["checks_total"],
                     roll["last_decided_at"], r["id"]))
                r.update(roll)
            out.append({
                "id": r["id"],
                "filename": r["filename"],
                "uploaded_at": r["uploaded_at"],
                "reran_at": r["reran_at"],
                "size_bytes": r["size_bytes"],
                "client": r["client"],
                "n_sheets": r["n_sheets"] or 0,
                "open_findings": r["open_findings"],
                "checks_ok": r["checks_ok"],
                "checks_total": r["checks_total"],
                "published_version": r["pub_version"],
                "stale": ((r["last_decided_at"] or "") > r["pub_at"]
                          if r["pub_version"] is not None else None),
            })
    return out


def set_client(analysis_id: str, client: Optional[str]) -> bool:
    """Assign an analysis to a client workspace (None/empty clears it)."""
    client = (client or "").strip() or None
    with _connect() as con:
        cur = con.execute("UPDATE analyses SET client = ? WHERE id = ?",
                          (client, analysis_id))
    return cur.rowcount > 0


def _week_start(day: str) -> Optional[str]:
    """Monday of the ISO week containing ``day`` (YYYY-MM-DD), or None."""
    try:
        d = _dt.date.fromisoformat(day)
    except (ValueError, TypeError):
        return None
    return (d - _dt.timedelta(days=d.weekday())).isoformat()


def stats() -> Dict[str, Any]:
    """Usage roll-up for the traction view — computed from stored metadata,
    no report payloads parsed beyond the cheap ``n_sheets`` field."""
    with _connect() as con:
        rows = con.execute(
            "SELECT uploaded_at, size_bytes, client, n_sheets, report "
            "FROM analyses"
        ).fetchall()

    by_day: Dict[str, int] = {}
    by_week: Dict[str, int] = {}
    by_client: Dict[str, int] = {}
    total_sheets = 0
    total_bytes = 0
    days_active = set()

    for r in rows:
        day = (r["uploaded_at"] or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + 1
            days_active.add(day)
            wk = _week_start(day)
            if wk:
                by_week[wk] = by_week.get(wk, 0) + 1
        label = r["client"] or "General"
        by_client[label] = by_client.get(label, 0) + 1
        total_bytes += int(r["size_bytes"] or 0)
        if r["n_sheets"] is not None:          # denormalized column
            total_sheets += int(r["n_sheets"])
        else:                                  # legacy row, parse once
            try:
                total_sheets += int(json.loads(r["report"]).get("n_sheets", 0) or 0)
            except Exception:
                pass

    days = sorted(d for d in by_day)
    return {
        "total_analyses": len(rows),
        "total_sheets": total_sheets,
        "total_bytes": total_bytes,
        "n_clients": sum(1 for c in by_client if c != "General"),
        "days_active": len(days_active),
        "first_upload": days[0] if days else None,
        "last_upload": days[-1] if days else None,
        "by_day": [{"date": d, "count": by_day[d]} for d in days],
        "by_week": [{"week": w, "count": by_week[w]} for w in sorted(by_week)],
        "by_client": sorted(
            ({"client": c, "count": n} for c, n in by_client.items()),
            key=lambda x: (-x["count"], x["client"])),
    }


def get_report(analysis_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as con:
        row = con.execute("SELECT report FROM analyses WHERE id = ?",
                          (analysis_id,)).fetchone()
    return json.loads(row["report"]) if row else None


def get_content(analysis_id: str) -> Optional[Tuple[str, bytes]]:
    """(filename, original upload bytes) for re-analysis."""
    with _connect() as con:
        row = con.execute("SELECT filename, content FROM analyses WHERE id = ?",
                          (analysis_id,)).fetchone()
    return (row["filename"], row["content"]) if row else None


def update_report(analysis_id: str, report: Dict[str, Any]) -> bool:
    roll = _rollup(report)
    with _connect() as con:
        cur = con.execute(
            "UPDATE analyses SET report = ?, reran_at = ?, open_findings = ?, "
            "n_sheets = ?, checks_ok = ?, checks_total = ?, last_decided_at = ? "
            "WHERE id = ?",
            (json.dumps(report, ensure_ascii=False), _now(),
             roll["open_findings"], roll["n_sheets"], roll["checks_ok"],
             roll["checks_total"], roll["last_decided_at"], analysis_id),
        )
    return cur.rowcount > 0


def delete_analysis(analysis_id: str) -> bool:
    with _connect() as con:
        con.execute("DELETE FROM shares WHERE analysis_id = ?", (analysis_id,))
        con.execute("DELETE FROM published WHERE analysis_id = ?", (analysis_id,))
        cur = con.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
    return cur.rowcount > 0


def publish(analysis_id: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Freeze an exec-only snapshot behind a stable public token.

    First publish mints the token and starts at version 1; republishing
    bumps the version, refreezes the snapshot, and REUSES the token — the
    link a client already has always shows the latest published version."""
    with _connect() as con:
        if con.execute("SELECT 1 FROM analyses WHERE id = ?",
                       (analysis_id,)).fetchone() is None:
            return None
        row = con.execute(
            "SELECT version, token FROM published WHERE analysis_id = ?",
            (analysis_id,)).fetchone()
        version = (row["version"] + 1) if row else 1
        token = row["token"] if row else secrets.token_urlsafe(16)
        published_at = _now()
        snapshot = {**snapshot, "version": version,
                    "published_at": published_at}
        con.execute(
            "INSERT INTO published (analysis_id, version, published_at, token, snapshot, opened_count) "
            "VALUES (?, ?, ?, ?, ?, 0) "
            "ON CONFLICT(analysis_id) DO UPDATE SET version = ?, "
            "published_at = ?, snapshot = ?",
            (analysis_id, version, published_at, token,
             json.dumps(snapshot, ensure_ascii=False),
             version, published_at, json.dumps(snapshot, ensure_ascii=False)),
        )
    return {"version": version, "published_at": published_at, "token": token}


def revoke_publish(analysis_id: str) -> bool:
    """Drop the published row; the public link dies immediately."""
    with _connect() as con:
        cur = con.execute("DELETE FROM published WHERE analysis_id = ?",
                          (analysis_id,))
    return cur.rowcount > 0


def open_published(token: str) -> Optional[Dict[str, Any]]:
    """The frozen snapshot behind a token (or None). Counts the open."""
    with _connect() as con:
        row = con.execute("SELECT analysis_id, snapshot FROM published "
                          "WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None
        con.execute("UPDATE published SET opened_count = opened_count + 1 "
                    "WHERE analysis_id = ?", (row["analysis_id"],))
    return json.loads(row["snapshot"])


def published_token_exists(token: str) -> bool:
    """Liveness check for serving the public page — no counter increment."""
    with _connect() as con:
        return con.execute("SELECT 1 FROM published WHERE token = ?",
                           (token,)).fetchone() is not None


def published_info(analysis_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as con:
        row = con.execute(
            "SELECT version, published_at, token, opened_count "
            "FROM published WHERE analysis_id = ?", (analysis_id,)).fetchone()
    return dict(row) if row else None


def is_stale(report: Dict[str, Any], published_at: str) -> bool:
    """A published copy is stale once ANY decision postdates it."""
    return any((d or {}).get("decided_at", "") > published_at
               for d in (report.get("decisions") or {}).values())
