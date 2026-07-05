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


def _db_path() -> Path:
    return Path(os.environ.get("LUMNIA_DB", "data/lumnia.db"))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    con.execute(_SCHEMA_SHARES)
    for migration in (                # migrate DBs created before the column
        "ALTER TABLE analyses ADD COLUMN sha256 TEXT",
        "ALTER TABLE analyses ADD COLUMN client TEXT",
    ):
        try:
            con.execute(migration)
        except sqlite3.OperationalError:
            pass
    return con


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def find_by_content(content: bytes) -> Optional[str]:
    """Id of an existing analysis of these exact bytes, if any."""
    digest = hashlib.sha256(content).hexdigest()
    with _connect() as con:
        row = con.execute("SELECT id FROM analyses WHERE sha256 = ?",
                          (digest,)).fetchone()
    return row["id"] if row else None


def save_analysis(filename: str, content: bytes, report: Dict[str, Any]) -> str:
    """Persist a new analysis; returns its id (also stamped into the report)."""
    analysis_id = uuid.uuid4().hex[:12]
    report = {**report, "id": analysis_id}
    with _connect() as con:
        con.execute(
            "INSERT INTO analyses (id, filename, uploaded_at, size_bytes, sha256, content, report) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (analysis_id, filename, _now(), len(content),
             hashlib.sha256(content).hexdigest(), content,
             json.dumps(report, ensure_ascii=False)),
        )
    return analysis_id


def list_analyses() -> List[Dict[str, Any]]:
    """Newest-first metadata for every stored analysis (no blobs).

    # DEBT: json.loads() of every report blob just to read n_sheets (same
    # in stats()). Fine at tens of analyses; at thousands the library page
    # gets slow and memory-spiky. Fix is an n_sheets column (one ALTER +
    # backfill) — do it when a workspace passes ~500 stored analyses.
    """
    with _connect() as con:
        rows = con.execute(
            "SELECT id, filename, uploaded_at, reran_at, size_bytes, client, report "
            "FROM analyses ORDER BY uploaded_at DESC, id"
        ).fetchall()
    out = []
    for r in rows:
        report = json.loads(r["report"])
        out.append({
            "id": r["id"],
            "filename": r["filename"],
            "uploaded_at": r["uploaded_at"],
            "reran_at": r["reran_at"],
            "size_bytes": r["size_bytes"],
            "client": r["client"],
            "n_sheets": report.get("n_sheets", 0),
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
            "SELECT uploaded_at, size_bytes, client, report FROM analyses"
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
    with _connect() as con:
        cur = con.execute(
            "UPDATE analyses SET report = ?, reran_at = ? WHERE id = ?",
            (json.dumps(report, ensure_ascii=False), _now(), analysis_id),
        )
    return cur.rowcount > 0


def delete_analysis(analysis_id: str) -> bool:
    with _connect() as con:
        con.execute("DELETE FROM shares WHERE analysis_id = ?", (analysis_id,))
        cur = con.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
    return cur.rowcount > 0


def create_share(analysis_id: str) -> Optional[str]:
    """Token for a read-only share link; idempotent per analysis."""
    with _connect() as con:
        if con.execute("SELECT 1 FROM analyses WHERE id = ?",
                       (analysis_id,)).fetchone() is None:
            return None
        row = con.execute("SELECT token FROM shares WHERE analysis_id = ?",
                          (analysis_id,)).fetchone()
        if row:
            return row["token"]
        token = secrets.token_urlsafe(16)
        con.execute("INSERT INTO shares (token, analysis_id, created_at) "
                    "VALUES (?, ?, ?)", (token, analysis_id, _now()))
    return token


def resolve_share(token: str) -> Optional[str]:
    """Analysis id behind a share token, or None if unknown/revoked."""
    with _connect() as con:
        row = con.execute("SELECT analysis_id FROM shares WHERE token = ?",
                          (token,)).fetchone()
    return row["analysis_id"] if row else None


def revoke_share(analysis_id: str) -> bool:
    with _connect() as con:
        cur = con.execute("DELETE FROM shares WHERE analysis_id = ?",
                          (analysis_id,))
    return cur.rowcount > 0
