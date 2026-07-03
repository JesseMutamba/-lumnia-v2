"""Step 8 — the storytelling engine's analytical core (Phase 1).

``detect_schema`` classifies a tidy table's columns into the vocabulary the
storytelling engine speaks:

* **time** — a parseable date column, with its grain (day/week/month/…);
* **dimensions** — groupable categories (Region, Subsector, a 0/1 flag);
* **entities** — identifier-like columns (Company_name, product codes) that
  serve as table grain, too many members to chart;
* **measures** — numeric columns, each with a *role* (revenue, volume,
  inventory… — the business-model patterns) and a *kind* that decides how it
  aggregates: ``amount`` sums, ``ratio``/``change_pct`` average.

``compute_story`` then runs the metric library over the table — headline
total, totals by dimension, trend + month-over-month + year-over-year when a
time column exists, pre-computed change metrics, top/bottom movers — and for
every staple it CANNOT compute it records an explicit gap naming what the
file would need. Fail honest: the gaps are first-class output, not silence.

Everything here is deterministic; no AI is involved at this layer.
"""
from __future__ import annotations

import re
import warnings
from typing import Any, Dict, List, Optional

import pandas as pd

from .eda import TARGET_NAME_PATTERNS
from .model import ROLE_PATTERNS

MAX_DIM_CARDINALITY = 50     # beyond this a text column is an entity, not a dim
MAX_MEMBERS_SHOWN = 12       # dimension members listed per metric (rest rolled up)
MAX_TREND_PERIODS = 60
MAX_DIMS_PER_METRIC = 3
MAX_CHANGE_MEASURES = 2
TOP_MOVERS = 5

INVENTORY_RX = re.compile(r"stock|inventaire|inventor|on.?hand", re.IGNORECASE)
CHANGE_RX = re.compile(
    r"growth|change|variation|croissance|[ée]volution|delta|Δ|\bmom\b|\byoy\b"
    r"|cagr|vs.?prev", re.IGNORECASE)
RATIO_RX = re.compile(
    r"%|percent|\bpct\b|margin|marge|ratio|score|index|indice|\brate\b|taux",
    re.IGNORECASE)

_NULLISH = {"", "none", "nan", "null"}


def _clean_text(s: pd.Series) -> pd.Series:
    t = s.dropna().astype(str).str.strip()
    return t[~t.str.lower().isin(_NULLISH)]


def _measure_role(name: str) -> Optional[str]:
    if INVENTORY_RX.search(name):
        return "inventory"
    for role, rx in ROLE_PATTERNS:
        if rx.search(name):
            return role
    return None


def _time_grain(dt: pd.Series) -> str:
    days = dt.dropna().sort_values().drop_duplicates().diff().dt.days.dropna()
    if days.empty:
        return "unknown"
    med = float(days.median())
    if med <= 1.5:
        return "day"
    if med <= 8:
        return "week"
    if med <= 45:
        return "month"
    if med <= 120:
        return "quarter"
    return "year"


def detect_schema(df: pd.DataFrame) -> Dict[str, Any]:
    """Classify every column; the result is small, JSON-safe and honest about
    what it could not classify (those columns are simply absent)."""
    n = len(df)
    time_dim: Optional[Dict[str, Any]] = None
    dims: List[Dict[str, Any]] = []
    entities: List[Dict[str, Any]] = []
    measures: List[Dict[str, Any]] = []

    for col in df.columns:
        name = str(col)
        s = df[col]
        nonnull = s.notna().sum()
        if nonnull < 3:
            continue
        num = pd.to_numeric(s, errors="coerce")
        if num.notna().sum() >= 0.8 * nonnull:
            uniq = set(num.dropna().unique())
            if uniq <= {0, 1} and len(uniq) >= 1:
                # a 0/1 flag is a category, not a quantity to sum
                dims.append({"name": name, "kind": "flag", "cardinality": len(uniq),
                             "members": sorted(int(v) for v in uniq)})
                continue
            kind = ("change_pct" if CHANGE_RX.search(name)
                    else "ratio" if RATIO_RX.search(name) else "amount")
            measures.append({"name": name, "kind": kind,
                             "role": _measure_role(name)})
            continue

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dt = pd.to_datetime(s, errors="coerce")
        if dt.notna().sum() >= 0.8 * nonnull and dt.nunique() >= 3:
            if time_dim is None:      # first qualifying date column wins
                span = dt.dropna()
                time_dim = {"name": name, "grain": _time_grain(dt),
                            "start": str(span.min().date()),
                            "end": str(span.max().date()),
                            "n_periods": int(dt.dt.to_period("M").nunique())}
            continue

        txt = _clean_text(s)
        card = int(txt.nunique())
        if card < 2:
            continue
        if card <= MAX_DIM_CARDINALITY and card <= 0.6 * n:
            dims.append({"name": name, "kind": "categorical", "cardinality": card,
                         "members": sorted(txt.unique().tolist())[:MAX_DIM_CARDINALITY]})
        elif card >= 0.8 * len(txt):
            entities.append({"name": name, "cardinality": card,
                             "avg_len": round(float(txt.str.len().mean()), 1)})

    dims.sort(key=lambda d: d["cardinality"])
    # the display entity is the wordiest identifier (names beat codes)
    entities.sort(key=lambda e: -e["avg_len"])
    return {"time": time_dim, "dimensions": dims, "entities": entities,
            "measures": measures}


# --------------------------------------------------------------------------
# metric library
# --------------------------------------------------------------------------

def _agg_how(kind: str) -> str:
    return "sum" if kind == "amount" else "mean"


def _headline_measure(schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # a measure from an unnamed column ("col_3") can compute but never leads
    named = [m for m in schema["measures"]
             if not re.fullmatch(r"col_\d+", m["name"])]
    ms = named or schema["measures"]
    for m in ms:
        if m["role"] == "revenue":
            return m
    unitish = re.compile(r"unit|unitaire|/|per\b|par\b|price|prix|rate|taux",
                         re.IGNORECASE)
    for m in ms:                      # money-named beats merely numeric...
        if m["kind"] == "amount" and TARGET_NAME_PATTERNS.search(m["name"]) \
                and not unitish.search(m["name"]):
            return m                  # ...but a unit price never headlines
    for m in ms:
        if m["kind"] == "amount" and TARGET_NAME_PATTERNS.search(m["name"]):
            return m
    for m in ms:
        if m["kind"] == "amount":
            return m
    return ms[0] if ms else None


def _round(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, 4)


def _by_dim(df: pd.DataFrame, measure: Dict[str, Any], dim: str,
            how: str) -> List[Dict[str, Any]]:
    vals = pd.to_numeric(df[measure["name"]], errors="coerce")
    grouped = (pd.DataFrame({"g": df[dim].astype(str).str.strip(), "v": vals})
               .dropna().groupby("g")["v"].agg([how, "count"]))
    grouped = grouped.sort_values(how, ascending=False)
    rows = [{"member": str(g), "value": _round(r[how]), "n": int(r["count"])}
            for g, r in grouped.iterrows()][:MAX_MEMBERS_SHOWN]
    if len(grouped) > MAX_MEMBERS_SHOWN and how == "sum":
        rest = grouped.iloc[MAX_MEMBERS_SHOWN:]
        rows.append({"member": "(others)", "value": _round(rest["sum"].sum()),
                     "n": int(rest["count"].sum())})
    return rows


def compute_story(df: pd.DataFrame, schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The default metric plan, computed. Returns None when there is nothing
    numeric to talk about."""
    head = _headline_measure(schema)
    if head is None or len(df) < 3:
        return None

    metrics: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    how = _agg_how(head["kind"])
    hvals = pd.to_numeric(df[head["name"]], errors="coerce")

    metrics.append({
        "id": "headline", "metric": how, "measure": head["name"],
        "role": head["role"], "value": _round(hvals.agg(how)),
        "n": int(hvals.notna().sum()),
    })

    for dim in schema["dimensions"][:MAX_DIMS_PER_METRIC]:
        metrics.append({
            "id": f"by_{dim['name']}", "metric": how, "measure": head["name"],
            "grain": dim["name"], "rows": _by_dim(df, head, dim["name"], how),
        })

    # ---- time family: trend, MoM, YoY — or an honest gap ------------------
    t = schema["time"]
    if t:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dt = pd.to_datetime(df[t["name"]], errors="coerce")
        per = dt.dt.to_period("M")
        series = (pd.DataFrame({"p": per, "v": hvals}).dropna()
                  .groupby("p")["v"].agg(how).sort_index())
        rows = [{"period": str(p), "value": _round(v)} for p, v in series.items()]
        truncated = len(rows) > MAX_TREND_PERIODS
        metrics.append({"id": "trend", "metric": "trend", "measure": head["name"],
                        "grain": "month", "rows": rows[-MAX_TREND_PERIODS:],
                        "truncated": truncated})
        if len(series) >= 2:
            prev, last = float(series.iloc[-2]), float(series.iloc[-1])
            metrics.append({
                "id": "mom", "metric": "mom_change", "measure": head["name"],
                "period": str(series.index[-1]),
                "value": _round(last - prev),
                "pct": _round((last / prev - 1) * 100) if prev else None,
            })
        else:
            gaps.append({"metric": "mom_change",
                         "reason": "only one period of data",
                         "requires": "at least two months of dated rows"})
        year_ago = series.index[-1] - 12
        if year_ago in series.index:
            base, last = float(series[year_ago]), float(series.iloc[-1])
            metrics.append({
                "id": "yoy", "metric": "yoy_change", "measure": head["name"],
                "period": str(series.index[-1]),
                "pct": _round((last / base - 1) * 100) if base else None,
            })
        else:
            gaps.append({"metric": "yoy_change",
                         "reason": "no data for the same month a year earlier",
                         "requires": "at least 13 months of dated rows"})
    else:
        for m in ("trend", "mom_change", "yoy_change"):
            gaps.append({"metric": m, "reason": "no date column found",
                         "requires": "a column of dates (daily or monthly)"})

    # ---- pre-computed change measures (Growth %, CAGR, Δ…) ----------------
    changes = [m for m in schema["measures"] if m["kind"] == "change_pct"]
    entity = schema["entities"][0]["name"] if schema["entities"] else None
    # a real category beats a 0/1 flag as the grouping for change metrics
    story_dim = next((d["name"] for d in schema["dimensions"]
                      if d["kind"] == "categorical"),
                     schema["dimensions"][0]["name"] if schema["dimensions"] else None)
    for ch in changes[:MAX_CHANGE_MEASURES]:
        if story_dim:
            metrics.append({
                "id": f"avg_{ch['name']}_by_{story_dim}", "metric": "mean",
                "measure": ch["name"], "grain": story_dim,
                "rows": _by_dim(df, ch, story_dim, "mean"),
            })
    if changes and entity:
        ch = changes[0]
        vals = pd.to_numeric(df[ch["name"]], errors="coerce")
        pair = (pd.DataFrame({"e": df[entity].astype(str).str.strip(),
                              "v": vals, "h": hvals})
                .dropna(subset=["e", "v"]).sort_values("v"))
        pick = lambda part: [
            {"entity": r["e"], "value": _round(r["v"]),
             "headline": _round(r["h"])} for _, r in part.iterrows()]
        metrics.append({
            "id": "movers", "metric": "top_movers", "measure": ch["name"],
            "grain": entity, "headline_measure": head["name"],
            "top": pick(pair.tail(TOP_MOVERS).iloc[::-1]),
            "bottom": pick(pair.head(TOP_MOVERS)),
        })
    elif not changes and not t:
        gaps.append({"metric": "top_movers",
                     "reason": "no change/growth column and no date column",
                     "requires": "either a growth column or dated rows"})

    # ---- inventory family --------------------------------------------------
    inv = next((m for m in schema["measures"] if m["role"] == "inventory"), None)
    if inv:
        grain = entity or (schema["dimensions"][0]["name"]
                           if schema["dimensions"] else None)
        if grain:
            vals = pd.to_numeric(df[inv["name"]], errors="coerce")
            low = (pd.DataFrame({"e": df[grain].astype(str).str.strip(), "v": vals})
                   .dropna().sort_values("v").head(TOP_MOVERS))
            metrics.append({
                "id": "low_stock", "metric": "lowest", "measure": inv["name"],
                "grain": grain,
                "rows": [{"entity": r["e"], "value": _round(r["v"])}
                         for _, r in low.iterrows()],
            })
    else:
        gaps.append({"metric": "stock_on_hand",
                     "reason": "no stock/inventory column found",
                     "requires": "a stock-on-hand or inventory column"})

    return {"headline_measure": head["name"], "metrics": metrics, "gaps": gaps}


def build_semantics(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Extraction-time entry point: schema + computed story for one table."""
    if df.empty or len(df) < 3:
        return None
    schema = detect_schema(df)
    if not schema["measures"]:
        return None
    story = compute_story(df, schema)
    if story is None:
        return None
    return {"schema": schema, "story": story}
