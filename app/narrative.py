"""Layer 3: the AI narrative — Claude phrases, the pipeline computes.

Everything numeric in Lumnia is deterministic: extraction, reconciliation,
EDA, the business model. This module adds the one thing templates can't do —
an executive narrative in plain language — WITHOUT giving the model room to
invent figures. The pipeline's verified numbers are serialized into a compact
fact sheet; Claude is instructed to use only those figures verbatim and the
result is returned as structured JSON (headline, narrative, watchouts).

Fail honest end to end:
* no ``ANTHROPIC_API_KEY`` configured -> the feature reports itself
  unavailable (503), nothing is faked;
* the API call fails -> 502 with the reason, never a canned paragraph;
* the model returns malformed JSON -> 502, we do not "repair" numbers.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_INSIGHTS = 6
MAX_METRIC_POINTS = 12

SYSTEM_PROMPT = """You are the narrative layer of Lumnia, a spreadsheet-audit \
tool. You will receive a FACTS block: verified figures computed by a \
deterministic pipeline from an uploaded workbook.

Write for a non-technical operator deciding what to do next.

HARD RULES — these are the product's integrity guarantee:
1. Use ONLY figures present in FACTS. You may reformat a number (thousands \
separators, rounding to fewer decimals, "1.2M" style) but NEVER compute new \
ones — no sums, growth rates, differences, or ratios of your own.
2. If something isn't in FACTS, don't claim it. No industry benchmarks, no \
invented context.
3. Mention data-quality problems from FACTS honestly; they are the point of \
the product, not something to soften.

Respond with ONLY a JSON object, no markdown fences:
{"headline": "<one factual sentence, <=110 chars>",
 "narrative": "<2-4 short paragraphs, plain language, <=180 words total>",
 "watchouts": ["<0-3 short caution bullets drawn from FACTS>"]}"""


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _series_line(role: str, m: Dict[str, Any], periods: List[str]) -> str:
    vals = m.get("values") or []
    pts = [f"{p}={_fmt(v)}" for p, v in zip(periods, vals) if v is not None]
    if len(pts) > MAX_METRIC_POINTS:
        pts = pts[:3] + ["..."] + pts[-3:]
    return f'{role} = "{m.get("label", "?")}": ' + ", ".join(pts)


def build_facts(report: Dict[str, Any],
                audit: Optional[Dict[str, Any]] = None) -> str:
    """Serialize the verified numbers into a compact, bounded fact sheet."""
    lines: List[str] = [
        f"FILE: {report.get('filename')} ({report.get('n_sheets')} sheets)"]

    model = report.get("model") or {}
    periods = [str(p) for p in model.get("periods") or []]
    if periods:
        lines.append(f"PERIOD AXIS: {periods[0]}..{periods[-1]} "
                     f"({len(periods)} periods), from sheet "
                     f"\"{model.get('source_sheet', '').strip()}\"")
    for role, m in (model.get("metrics") or {}).items():
        lines.append(_series_line(role, m, periods))
    for name, vals in (model.get("derived") or {}).items():
        lines.append(_series_line(f"derived {name}", {"label": name,
                                                      "values": vals}, periods))
    for b in (model.get("breakdowns") or [])[:2]:
        items = ", ".join(f'"{i["label"]}"={_fmt(i["value"])}'
                          for i in b["items"][:5])
        lines.append(f'TOP ITEMS on sheet "{b["sheet"]}" '
                     f'(by {b.get("value_col", "?")}): {items}')

    for ins in (report.get("insights") or [])[:MAX_INSIGHTS]:
        txt = ins.get("message") or ""
        if txt:
            lines.append(f"INSIGHT [{ins.get('type', '?')}, "
                         f"{ins.get('severity', '?')}]: {txt}")

    if audit:
        lines.append(
            f"AUDIT: {audit.get('n_mismatched_relations', 0)} broken "
            f"relations (total discrepancy "
            f"{_fmt(audit.get('total_abs_delta', 0))}), "
            f"{audit.get('n_verified_relations', 0)} verified, "
            f"{audit.get('n_unverified_relations', 0)} unverified")

    errors = [s["name"] for s in report.get("sheets", [])
              if s.get("orientation") == "error"]
    if errors:
        lines.append(f"SHEETS THAT FAILED ANALYSIS: {', '.join(errors)}")
    return "\n".join(lines)


def _call_claude(facts: str) -> str:
    """One Messages API call; returns the raw text of the first block."""
    resp = httpx.post(
        API_URL,
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": os.environ.get("LUMNIA_NARRATIVE_MODEL", DEFAULT_MODEL),
            "max_tokens": 700,
            "temperature": 0.2,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user",
                          "content": f"FACTS:\n{facts}"}],
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(b.get("text", "") for b in data.get("content", []))


class NarrativeError(RuntimeError):
    pass


def generate_narrative(report: Dict[str, Any],
                       audit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fact sheet -> Claude -> validated narrative payload."""
    facts = build_facts(report, audit)
    try:
        raw = _call_claude(facts)
    except Exception as exc:
        raise NarrativeError(f"Narrative call failed: {exc}") from exc

    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        out = json.loads(text)
        assert isinstance(out.get("headline"), str)
        assert isinstance(out.get("narrative"), str)
    except Exception as exc:
        raise NarrativeError(
            f"Narrative response was not valid JSON: {exc}") from exc

    watch = out.get("watchouts")
    return {
        "headline": out["headline"].strip(),
        "narrative": out["narrative"].strip(),
        "watchouts": [str(w) for w in watch][:3] if isinstance(watch, list) else [],
        "model": os.environ.get("LUMNIA_NARRATIVE_MODEL", DEFAULT_MODEL),
    }
