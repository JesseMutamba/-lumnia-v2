"""The exec-only publish snapshot.

``build_exec_snapshot`` freezes exactly what the executive page shows —
aggregates, the KPI strip, ONE story chart, next steps, the honest-gaps
footnote — and nothing else. The public endpoint serves this dict verbatim;
because full sheets / mismatch detail / data-profile material are never put
IN the snapshot, the public path cannot leak them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .findings import aggregate_findings

MAX_STEPS = 4
MAX_SERIES = 6


def _kpis(report: Dict[str, Any], agg: Dict[str, Any]) -> Dict[str, Any]:
    rows = 0
    cells = 0
    nulls = 0
    tables = 0
    for sheet in report.get("sheets", []):
        tidies = [sheet.get("tidy")] + \
            [p.get("tidy") for p in sheet.get("panels") or []]
        for tidy in tidies:
            if not tidy:
                continue
            rows += tidy.get("n_records", 0) or 0
            eda = tidy.get("eda") or {}
            rc, cc = eda.get("row_count", 0) or 0, eda.get("column_count", 0) or 0
            if rc:
                tables += 1
                cells += rc * cc
                for info in (eda.get("missingness") or {}).values():
                    nulls += info.get("count", 0) or 0
    return {
        "rows": rows,
        "tables": tables,
        "fill_pct": round(100 - nulls / cells * 100) if cells else None,
        "checks_ok": agg["n_verified_relations"],
        "checks_total": agg["n_verified_relations"] + agg["n_mismatched_relations"],
        "at_stake": agg["total_abs_delta"],
    }


def _story_chart(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The workbook's primary timeseries — same pick the exec overview
    makes: the chart with the most periods."""
    best = None
    for sheet in report.get("sheets", []):
        tidies = [sheet.get("tidy")] + \
            [p.get("tidy") for p in sheet.get("panels") or []]
        for tidy in tidies:
            chart = tidy and (tidy.get("summary") or {}).get("chart")
            if not chart or chart.get("kind") != "timeseries":
                continue
            if best is None or len(chart.get("periods", [])) > \
                    len(best["chart"].get("periods", [])):
                best = {"sheet": sheet["name"], "chart": chart}
    if best is None:
        return None
    chart = best["chart"]
    series = [{"label": s.get("label"), "values": s.get("values")}
              for s in chart.get("series", [])[:MAX_SERIES]]
    if chart.get("other"):
        series.append({"label": chart["other"].get("label"),
                       "values": chart["other"].get("values")})
    return {"sheet": best["sheet"],
            "periods": chart.get("periods", []),
            "series": series}


def _next_steps(agg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Structured (language-neutral) step data; the page phrases them."""
    steps: List[Dict[str, Any]] = []
    for f in agg["findings"][:MAX_STEPS - 1]:
        steps.append({"kind": "confirm", "sheet": f["sheet"],
                      "n_mismatched": f.get("n_mismatched", 0),
                      "n_checked": f.get("n_checked", 0),
                      "delta": f.get("total_abs_delta", 0)})
    if agg["n_unverified_relations"]:
        steps.append({"kind": "eyeball",
                      "count": agg["n_unverified_relations"]})
    return steps[:MAX_STEPS]


def build_exec_snapshot(report: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: report dict in, exec-only snapshot dict out. `version` and
    `published_at` are stamped by storage.publish()."""
    agg = aggregate_findings(report)
    return {
        "filename": report.get("filename"),
        "n_sheets": report.get("n_sheets"),
        "verdict": {
            "mismatched": agg["n_mismatched_relations"],
            "unverified": agg["n_unverified_relations"],
            "verified": agg["n_verified_relations"],
            "at_stake": agg["total_abs_delta"],
        },
        "kpis": _kpis(report, agg),
        "chart": _story_chart(report),
        "steps": _next_steps(agg),
        "gaps": {"unverified": agg["n_unverified_relations"]},
    }
