"""The exec-only publish snapshot.

``build_exec_snapshot`` freezes exactly what the executive page shows — the
business model (role-tagged aggregate series + derived indicators), the audit
counts behind the trust chip, and ONE story chart for model-less files — and
nothing else. The public endpoint serves this dict verbatim; because full
sheets / mismatch detail / data-profile material — and raw line-item
breakdowns — are never put IN the snapshot, the public path cannot leak them.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .findings import aggregate_findings

MAX_SERIES = 6


def _strip_sheets(block: Dict[str, Any]) -> Dict[str, Any]:
    """Drop workbook sheet names from a model block — the exec page never
    shows them, and the public payload should carry no file structure."""
    out = {k: v for k, v in block.items() if k != "source_sheet"}
    metrics = out.get("metrics")
    if isinstance(metrics, dict):
        out["metrics"] = {role: {mk: mv for mk, mv in s.items() if mk != "sheet"}
                          for role, s in metrics.items()}
    return out


def _public_model(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The business model, minus ``breakdowns`` (raw line-item labels — the
    per-cell detail the public snapshot must never expose) and minus every
    sheet name. The aggregate role series and derived indicators are safe:
    they are the numbers the exec page draws."""
    model = report.get("model")
    if not model:
        return None
    pub = _strip_sheets({k: v for k, v in model.items() if k != "breakdowns"})
    if isinstance(pub.get("monthly"), dict):
        pub["monthly"] = _strip_sheets(pub["monthly"])
    return pub


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
    # no sheet name — the public page carries no file structure
    return {"periods": chart.get("periods", []), "series": series}


def build_exec_snapshot(report: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: report dict in, exec-only snapshot dict out. `version` and
    `published_at` are stamped by storage.publish(). The public page renders
    the same business-voice dashboard as the in-app exec preview: the model
    lights the KPI strip and charts; the audit counts drive the trust chip and
    the verdict; the story chart is the fallback when nothing role-tags."""
    agg = aggregate_findings(report)
    model = _public_model(report)
    return {
        "filename": report.get("filename"),
        "n_sheets": report.get("n_sheets"),
        # the counts the exec voice reads — never per-finding cell detail
        "audit": {
            "n_mismatched_relations": agg["n_mismatched_relations"],
            "n_unverified_relations": agg["n_unverified_relations"],
            "n_verified_relations": agg["n_verified_relations"],
            "total_abs_delta": agg["total_abs_delta"],
        },
        # kept for the verdict summary line (back-compat with older readers)
        "verdict": {
            "mismatched": agg["n_mismatched_relations"],
            "unverified": agg["n_unverified_relations"],
            "verified": agg["n_verified_relations"],
            "at_stake": agg["total_abs_delta"],
        },
        "model": model,
        # the story chart is only the fallback when nothing role-tags; when a
        # model exists the dashboard draws from it and this stays out
        "chart": None if model else _story_chart(report),
    }
