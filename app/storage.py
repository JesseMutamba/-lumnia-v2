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
import json
import os
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
    content     BLOB NOT NULL,
    report      TEXT NOT NULL
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
    return con


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def save_analysis(filename: str, content: bytes, report: Dict[str, Any]) -> str:
    """Persist a new analysis; returns its id (also stamped into the report)."""
    analysis_id = uuid.uuid4().hex[:12]
    report = {**report, "id": analysis_id}
    with _connect() as con:
        con.execute(
            "INSERT INTO analyses (id, filename, uploaded_at, size_bytes, content, report) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (analysis_id, filename, _now(), len(content), content,
             json.dumps(report, ensure_ascii=False)),
        )
    return analysis_id


def list_analyses() -> List[Dict[str, Any]]:
    """Newest-first metadata for every stored analysis (no blobs)."""
    with _connect() as con:
        rows = con.execute(
            "SELECT id, filename, uploaded_at, reran_at, size_bytes, report "
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
            "n_sheets": report.get("n_sheets", 0),
        })
    return out


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
        cur = con.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
    return cur.rowcount > 0
