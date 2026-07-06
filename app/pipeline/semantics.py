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
import unicodedata
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
# identifier columns: "ID", "PLAYER_ID", "code client"… — labels, never sums
ID_NAME_RX = re.compile(r"(?:^|[_\s.\-])id(?:entifiant)?$|^id$|(?:^|[_\s.\-])"
                        r"code(?:$|[_\s.\-])", re.IGNORECASE)
# rank/position columns are ordinals — "Total PTS_RANK" means nothing
RANK_NAME_RX = re.compile(r"(?:^|[_\s.\-])(?:rank|rang|classement)$",
                          re.IGNORECASE)
# mean-only quantities: an average age informs, a summed age never does
MEANISH_RX = re.compile(r"(?:^|[_\s.\-])(?:age|âge)$|ancien+et[ée]",
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
    year_dim: Optional[Dict[str, Any]] = None
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
            vals = num.dropna()
            # a column of calendar years (2019, 2020…) is a period label,
            # not a quantity — summing years is never meaningful. It IS a
            # candidate time axis (adopted below only if no date column).
            if (vals.between(1900, 2100).mean() >= 0.9
                    and (vals % 1 == 0).mean() >= 0.95
                    and vals.nunique() >= 3):
                if year_dim is None:
                    yrs = vals.astype(int)
                    year_dim = {"name": name, "grain": "year",
                                "start": str(int(yrs.min())),
                                "end": str(int(yrs.max())),
                                "n_periods": int(yrs.nunique())}
                continue
            # *_ID columns are identifiers, not quantities — a summed
            # PLAYER_ID is arithmetic without meaning; ranks are ordinals
            if ID_NAME_RX.search(name) or RANK_NAME_RX.search(name):
                continue
            uniq = set(vals.unique())
            if uniq <= {0, 1}:
                # a 0/1 flag is a category, not a quantity to sum — but a
                # single-valued flag is a constant: nothing to group by
                if len(uniq) == 2:
                    dims.append({"name": name, "kind": "flag", "cardinality": 2,
                                 "members": [0, 1]})
                continue
            kind = ("change_pct" if CHANGE_RX.search(name)
                    else "ratio" if RATIO_RX.search(name)
                    else "mean" if MEANISH_RX.search(name)   # AGE averages
                    else "amount")
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

    if time_dim is None:              # a real date column always wins
        time_dim = year_dim
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
    for m in ms:                      # any plain amount beats a unit price:
        if m["kind"] == "amount" and not unitish.search(m["name"]):
            return m                  # summing unit costs means nothing
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

    # 0/1 flags stay in the schema for analysts but never become narrative
    # subjects — "1 accounts for 93% of…" is a sentence about a checkbox
    real_dims = [d for d in schema["dimensions"] if d.get("kind") != "flag"]
    for dim in real_dims[:MAX_DIMS_PER_METRIC]:
        metrics.append({
            "id": f"by_{dim['name']}", "metric": how, "measure": head["name"],
            "grain": dim["name"], "rows": _by_dim(df, head, dim["name"], how),
        })

    # ---- time family: trend, MoM, YoY — or an honest gap ------------------
    t = schema["time"]
    yrs = (pd.to_numeric(df[t["name"]], errors="coerce")
           if t and t.get("grain") == "year" else None)
    if yrs is not None and yrs.dropna().between(1900, 2100).mean() >= 0.9:
        # bare year numbers (2025, 2026…) group by the year itself —
        # to_datetime would misread the integers as epoch nanoseconds
        series = (pd.DataFrame({"p": yrs.astype("Int64"), "v": hvals}).dropna()
                  .groupby("p")["v"].agg(how).sort_index())
        rows = [{"period": str(int(p)), "value": _round(v)}
                for p, v in series.items()]
        metrics.append({"id": "trend", "metric": "trend", "measure": head["name"],
                        "grain": "year", "rows": rows[-MAX_TREND_PERIODS:],
                        "truncated": len(rows) > MAX_TREND_PERIODS})
        if len(series) >= 2:      # on yearly data, last vs previous IS YoY
            base, last = float(series.iloc[-2]), float(series.iloc[-1])
            metrics.append({
                "id": "yoy", "metric": "yoy_change", "measure": head["name"],
                "period": str(int(series.index[-1])),
                "pct": _round((last / base - 1) * 100) if base else None,
            })
        else:
            gaps.append({"metric": "yoy_change",
                         "reason": "only one year of data",
                         "requires": "at least two years of rows"})
        # no MoM gap: month-over-month is not applicable to yearly data,
        # not something more rows of it could ever supply
    elif t:
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


# --------------------------------------------------------------------------
# Phase 2: the brief -> metric plan matcher
# --------------------------------------------------------------------------
# Each intent maps a question pattern (FR first, EN too) onto selectors over
# the computed story: which present metric ids answer it, and which gap names
# explain why it can't be answered.

_INTENTS = [
    # specific intents first: a question about stockouts should lead with
    # the stock gap, not the generic product-performance one
    ("stock", re.compile(
        r"stock|inventaire|inventor|rupture|out of stock|shortage|p[ée]nurie",
        re.IGNORECASE),
     lambda mid: mid == "low_stock",
     {"stock_on_hand"}),
    ("trend", re.compile(
        r"trend|tendance|[ée]volu|over time|au fil|progress|croissance"
        r"|how is .* (doing|going)|comment", re.IGNORECASE),
     lambda mid: mid in ("trend", "mom", "yoy"),
     {"trend", "mom_change", "yoy_change"}),
    ("performance", re.compile(
        r"best|worst|top|bottom|perform|meilleur|pire|classement|rank"
        r"|produits?|products?|entreprises?|compan(y|ies)", re.IGNORECASE),
     lambda mid: mid == "movers" or mid.startswith("avg_"),
     {"top_movers"}),
    ("breakdown", re.compile(
        r"where|o[uù]\b|r[ée]gion|by |par |split|r[ée]partition|breakdown"
        r"|which|quel(le)?s?", re.IGNORECASE),
     lambda mid: mid.startswith("by_"),
     set()),
    ("amount", re.compile(
        r"revenu|revenue|ventes|sales|chiffre|montant|total|how much|combien",
        re.IGNORECASE),
     lambda mid: mid == "headline" or mid.startswith("by_"),
     {"trend"}),
]


# Generic intents ("split by…", "how much…") may only claim metrics from a
# story whose OWN vocabulary overlaps the question — a harvest question must
# never be "answered" with salary charts just because both can be split.
_GENERIC_INTENTS = {"breakdown", "amount"}
_STOPWORDS = set("""
les des une dans pour avec sont est nos vos votre notre nous vous elle ils
par sur vers jour jours mois annee annees comment quel quelle quels quelles
lequel laquelle plus moins tres bien peut evolue evoluent descend rupture
the and for our are is how which what who does most least best worst much
many per day days month months year years trending down toward out are
""".split())


def _norm_tokens(text: Any) -> set:
    t = unicodedata.normalize("NFD", str(text or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return {w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in _STOPWORDS}


def _story_vocab(st: Dict[str, Any]) -> set:
    toks = _norm_tokens(st.get("sheet")) | _norm_tokens(st.get("headline_measure"))
    for m in st.get("metrics", []):
        toks |= _norm_tokens(m.get("measure")) | _norm_tokens(m.get("grain"))
        for r in (m.get("rows") or [])[:15]:
            toks |= _norm_tokens(r.get("member") or r.get("entity"))
        for r in (m.get("top") or [])[:5]:
            toks |= _norm_tokens(r.get("entity"))
    return toks


def _affinity(qtoks: set, vocab: set) -> int:
    """Word overlap with a light prefix stem ('produits' ~ 'production')."""
    n = 0
    for q in qtoks:
        if q in vocab or any(len(q) >= 5 and len(v) >= 5 and q[:5] == v[:5]
                             for v in vocab):
            n += 1
    return n


def plan_from_brief(brief: Dict[str, Any],
                    stories: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Match every brief question against ALL of the workbook's stories.

    Real workbooks answer different questions on different sheets (harvest
    on one, fuel stock on another), so each question is scored against each
    table's story and lands on the sheet that answers it best. Metric ids
    are qualified ``s{story_index}:{metric_id}``.

    Statuses stay honest: ``answerable``, ``partial``, ``unanswerable``
    (with what the file would need), ``unmatched``.
    """
    if isinstance(stories, dict):                 # tolerate a single story
        stories = [stories]

    vocabs = [_story_vocab(st) for st in stories]
    questions = []
    claimed: set = set()
    for q in brief.get("questions", []):
        selectors = [(name, sel, gap_names)
                     for name, rx, sel, gap_names in _INTENTS if rx.search(q)]
        qtoks = _norm_tokens(q)
        best_si, best_ids, best_aff = None, [], -1
        for si, st in enumerate(stories):
            present = {m["id"] for m in st.get("metrics", [])}
            aff = _affinity(qtoks, vocabs[si])
            ids = sorted({mid for name, sel, _g in selectors
                          for mid in present if sel(mid)
                          and (aff > 0 or name not in _GENERIC_INTENTS)})
            if (len(ids), aff) > (len(best_ids), best_aff) and ids:
                best_si, best_ids, best_aff = si, ids, aff
        hit_ids = [f"s{best_si}:{i}" for i in best_ids] if best_si is not None else []
        claimed.update(hit_ids)

        # missing pieces are reported from the answering sheet (or the spine),
        # deduped by requirement — three time-gaps need one dates column, once
        gap_src = stories[best_si if best_si is not None else 0] if stories else {}
        gaps = {g["metric"]: g for g in gap_src.get("gaps", [])}
        seen: set = set()
        missing = [gaps[g] for _n, _sel, gap_names in selectors
                   for g in gap_names if g in gaps
                   and not (gaps[g]["requires"] in seen
                            or seen.add(gaps[g]["requires"]))]

        if hit_ids and missing:
            status = "partial"
        elif hit_ids:
            status = "answerable"
        elif missing:
            status = "unanswerable"
        else:
            status = "unmatched"
        questions.append({
            "question": q, "status": status, "metrics": hit_ids,
            "sheet": (stories[best_si]["sheet"] if best_si is not None
                      and stories[best_si].get("sheet") else None),
            "missing": [{"metric": g["metric"], "requires": g["requires"]}
                        for g in missing]})

    spine_avail = [f"s0:{m['id']}" for m in
                   (stories[0].get("metrics", []) if stories else [])]
    return {
        "questions": questions,
        "also_available": sorted(set(spine_avail) - claimed),
        "approved": None,     # set by the approval endpoint
    }


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


# --------------------------------------------------------------------------
# recommended goals & questions: derived from what the file CAN answer
# --------------------------------------------------------------------------

MAX_SUGGESTED_GOALS = 3
MAX_SUGGESTED_QUESTIONS = 6


def suggest_brief(stories: List[Dict[str, Any]], lang: str = "en"
                  ) -> Dict[str, List[str]]:
    """Goals and questions generated from the workbook's computed stories.

    Every suggestion is phrased with the vocabulary the intent matcher
    recognizes AND references a metric that already exists — so a suggested
    question is answerable (or honestly partial) by construction. No AI,
    no key: the file itself writes the menu.
    """
    fr = lang == "fr"
    goals: List[str] = []
    questions: List[str] = []
    seen_g: set = set()
    seen_q: set = set()

    def add_goal(text: str) -> None:
        if text not in seen_g and len(goals) < MAX_SUGGESTED_GOALS:
            seen_g.add(text)
            goals.append(text)

    def add_q(key: tuple, text: str) -> None:
        # dedupe on the rendered text too: two sheets with the same grain
        # would otherwise suggest the identical question twice
        if key in seen_q or text in seen_q:
            return
        if len(questions) < MAX_SUGGESTED_QUESTIONS:
            seen_q.update((key, text))
            questions.append(text)

    stories = stories or []
    roles = {m.get("role") for st in stories
             for m in st.get("schema", {}).get("measures", []) if m.get("role")}
    has_stock = any(m["id"] == "low_stock"
                    for st in stories for m in st.get("metrics", []))
    if "revenue" in roles:
        add_goal("Augmenter les revenus" if fr else "Increase revenue")
    if "inventory" in roles or has_stock:
        add_goal("Éviter les ruptures de stock" if fr
                 else "Avoid stock shortages")
    if "volume" in roles:
        add_goal("Augmenter la production" if fr else "Increase production")
    if "capex" in roles:
        add_goal("Maîtriser les investissements" if fr
                 else "Control investment")
    if not goals:
        add_goal("Suivre la performance mensuelle" if fr
                 else "Track monthly performance")

    for st in stories:
        ids = {m["id"]: m for m in st.get("metrics", [])}
        meas = (st.get("headline_measure") or "").strip()[:32]
        rich = False
        if "trend" in ids and meas:
            yearly = ids["trend"].get("grain") == "year"
            step = ("année par année" if yearly else "mois par mois") if fr \
                else ("year by year" if yearly else "month by month")
            add_q(("trend", meas),
                  f"Comment évolue {meas} {step} ?" if fr
                  else f"How is {meas} trending {step}?")
            rich = True
        if "movers" in ids:
            grain = (ids["movers"].get("grain") or "").strip()[:32] \
                or ("série" if fr else "series")
            add_q(("movers", st.get("sheet")),
                  f"Quel {grain} performe le mieux, et lequel sous-performe ?"
                  if fr else
                  f"Which {grain} is performing best, and which worst?")
            rich = True
        if "low_stock" in ids and ids["low_stock"].get("rows"):
            lbl = str(ids["low_stock"]["rows"][0].get("entity", ""))[:32]
            add_q(("stock", st.get("sheet")),
                  f"Le stock ({lbl}) descend-il vers la rupture ?" if fr
                  else f"Is stock ({lbl}) heading toward a shortage?")
            rich = True
        for mid, m in ids.items():
            if mid.startswith("by_") and not re.fullmatch(r"by_col_\d+", mid):
                dim = (m.get("grain") or "").strip()[:32]
                subject = meas or dim
                add_q(("by", subject, dim),
                      f"Quelle est la répartition de {subject} par {dim} ?"
                      if fr else f"How is {subject} split by {dim}?")
                rich = True
                break                          # one breakdown per story
        if not rich and "headline" in ids and meas:
            add_q(("total", meas),
                  f"Quel est le total de {meas} ?" if fr
                  else f"What is the total {meas}?")

    return {"goals": goals, "questions": questions}


# --------------------------------------------------------------------------
# matrix (cross-tab) sheets: the long form drives the same story engine
# --------------------------------------------------------------------------

MAX_MATRIX_SERIES = 24
# TOTAL/CUMUL rows are derived aggregates of the other series — including
# them in sums would double-count, exactly like a totals row in a tidy table
DERIVED_SERIES_RX = re.compile(r"total|cumul|sous.?tot|grand.?tot", re.IGNORECASE)


def build_matrix_semantics(sheet_name: str, period_values: List[Any],
                           series_values: Dict[str, Dict[int, float]]
                           ) -> Optional[Dict[str, Any]]:
    """Semantics for a DATE-axis matrix.

    The unpivoted long form (Date x Série x value) feeds the same schema
    detector and metric engine as a tidy table. The measure is named after
    the sheet — that's honestly what the numbers are ("ENREGISTREMENT
    PRODUCTION") — which also lets the role patterns tag it (production ->
    volume, carburant/stock -> inventory…).

    Matrix-specific intelligence:
    * TOTAL/CUMUL series are derived aggregates — excluded from the data
      (their sums would double-count) exactly like totals rows;
    * stock-labeled series become a ``low_stock`` metric (latest level per
      series) instead of being summed as flows;
    * per-series month-over-month deltas become the ``movers`` metric —
      "which série gained / lost the most last month".
    """
    measure = (str(sheet_name).strip() or "Valeur")[:60]
    ranked = sorted(series_values.items(),
                    key=lambda kv: -sum(abs(v) for v in kv[1].values()))
    ranked = ranked[:MAX_MATRIX_SERIES]

    stock = [(lbl, b) for lbl, b in ranked
             if INVENTORY_RX.search(str(lbl)) and b]
    flows = [(lbl, b) for lbl, b in ranked
             if not DERIVED_SERIES_RX.search(str(lbl))
             and not INVENTORY_RX.search(str(lbl))]

    rows = [{"Date": period_values[k], "Série": str(lbl), measure: v}
            for lbl, bucket in flows for k, v in bucket.items()
            if period_values[k] is not None and v is not None]
    if len(rows) < 6:
        return None
    df = pd.DataFrame(rows)
    sem = build_semantics(df)
    if sem is None:
        return None
    story = sem["story"]

    if stock:
        latest = [{"entity": str(lbl), "value": _round(b[max(b)])}
                  for lbl, b in stock]
        latest.sort(key=lambda r: (r["value"] is None, r["value"]))
        story["metrics"].append({
            "id": "low_stock", "metric": "latest", "measure": measure,
            "grain": "Série", "rows": latest[:TOP_MOVERS]})
        story["gaps"] = [g for g in story["gaps"]
                         if g["metric"] != "stock_on_hand"]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        months = pd.to_datetime(df["Date"], errors="coerce").dt.to_period("M")
    grouped = (df.assign(_m=months).dropna(subset=["_m"])
               .groupby(["Série", "_m"])[measure].sum())
    deltas = []
    for lbl in grouped.index.get_level_values(0).unique():
        s = grouped.loc[lbl].sort_index()
        if len(s) >= 2:
            deltas.append({"entity": str(lbl),
                           "value": _round(float(s.iloc[-1]) - float(s.iloc[-2])),
                           "headline": _round(s.iloc[-1])})
    if len(deltas) >= 2:
        deltas.sort(key=lambda r: -(r["value"] or 0))
        story["metrics"].append({
            "id": "movers", "metric": "top_movers",
            "measure": f"Δ {measure}"[:48], "grain": "Série",
            "top": deltas[:TOP_MOVERS],
            "bottom": deltas[-TOP_MOVERS:][::-1]})
        story["gaps"] = [g for g in story["gaps"]
                         if g["metric"] != "top_movers"]
    return sem
