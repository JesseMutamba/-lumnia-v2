"""Workbook-level findings: aggregate every reconciliation check across all
sheets and panels of a stored analysis into one audit report.

Per-sheet, per-table findings are useful but buried; the question a client
actually asks is "where does this workbook disagree with itself, and by how
much?" — one list, ranked by money impact, each entry pointing at the sheet
(and panel) plus the Excel rows involved.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

# Per-finding decision states. `open` is the default (nobody has looked);
# `approved` = fix will be applied on corrected-copy export; `flagged` =
# reviewed and kept as-is.
DECISIONS = ("open", "approved", "flagged")


def _finding_id(sheet: str, panel: Any, f: Dict[str, Any]) -> str:
    """Deterministic id: the same relation gets the same id on every
    aggregation and across reruns, so persisted decisions keep pointing at
    the same problem. Recipe shared with the corrected-copy spec."""
    raw = f"{sheet}|{panel or '-'}|{f.get('target')}|{f.get('formula')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _check_sources(sheet: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """(panel_label, tidy_table) pairs holding checks for one sheet report."""
    out = []
    if sheet.get("tidy"):
        out.append((None, sheet["tidy"]))
    for p in sheet.get("panels") or []:
        if not p.get("tidy"):
            continue
        if "col_start" in p:                 # side-by-side panel
            label = f"cols {p['col_start']}-{p['col_end']}"
        else:                                # vertically stacked band
            label = f"rows {p['row_start']}-{p['row_end']}"
        out.append((label, p["tidy"]))
    return out


def aggregate_findings(report: Dict[str, Any]) -> Dict[str, Any]:
    """Fold every check in an analysis report into one ranked audit view.

    Every entry carries a stable ``id`` and its persisted ``decision``
    (from ``report["decisions"]``, default ``open``)."""
    decisions = report.get("decisions") or {}
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "mismatch": [], "ok": [], "unverified": []}
    seen: Dict[str, int] = {}
    for sheet in report.get("sheets", []):
        for panel, tidy in _check_sources(sheet):
            for f in tidy.get("checks") or []:
                fid = _finding_id(sheet["name"], panel, f)
                # two identical relations on one sheet must not share an id
                n = seen.get(fid, 0)
                seen[fid] = n + 1
                if n:
                    fid = f"{fid}-{n + 1}"
                entry = {"id": fid,
                         "decision": (decisions.get(fid) or {}).get(
                             "decision", "open"),
                         "sheet": sheet["name"], "panel": panel, **f}
                buckets.get(f.get("status"), buckets["unverified"]).append(entry)

    mismatched = buckets["mismatch"]
    mismatched.sort(key=lambda f: -f.get("total_abs_delta", 0))
    return {
        "n_mismatched_relations": len(mismatched),
        "n_verified_relations": len(buckets["ok"]),
        "n_unverified_relations": len(buckets["unverified"]),
        "total_abs_delta": round(
            sum(f.get("total_abs_delta", 0) for f in mismatched), 4),
        "findings": mismatched,
        "verified": buckets["ok"],
        "unverified": buckets["unverified"],
    }


def count_open(report: Dict[str, Any]) -> int:
    """Findings still needing an eye: mismatched + unverified entries whose
    decision is ``open``. This is the number the publish gate checks and
    the Home rollup shows."""
    agg = aggregate_findings(report)
    return sum(1 for f in agg["findings"] + agg["unverified"]
               if f["decision"] == "open")
