"""The journal engine: contract-based audit of dual cash journals.

Implements LUMNIA_report_pipeline_spec.md for SUIVI-FINANCES-shaped
workbooks: a site cash journal (``JC Consolidé``) mirrored by a
head-office journal (``JC DGO``), a code glossary laid out in the
``EXTRACTION MOIS`` sheet, and the recording conventions that make CDF
``Sorties`` the single complete spend series (dual-currency mirroring).

parse -> validate (V1–V8) -> aggregate (code × month) -> payload.

Everything fails honest: a workbook that doesn't match the contract
simply gets no journal block; a broken tie-out is a BLOCKER finding,
never an exception — an upload must not 500 because the books are wrong,
saying so is the product.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, List, Optional

import pandas as pd

PIVOT_DEFAULT = 2250.0
HEADER_ROW = 9                     # 0-indexed: contract says header at row 10

SITE_SHEET = "JC Consolidé"
DGO_SHEET = "JC DGO"
EXTRACTION_SHEET = "EXTRACTION MOIS"

# transfer / neutral codes: never cost, spend under them is a finding (V3)
NON_COST_CODES = {"REPORT", "Banque", "AR"}

# V1 matching tolerances, from the spec: ±0.5 USD or ±1,200 CDF
V1_TOL_USD = 0.5
V1_TOL_CDF = 1200.0

UNMAPPED = "UNMAPPED"


def _clean(v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def _num(v: Any) -> float:
    try:
        x = float(v)
        return 0.0 if math.isnan(x) else round(x, 2)
    except (TypeError, ValueError):
        return 0.0


def _row_hash(t: Dict[str, Any]) -> str:
    key = "|".join(str(t[k]) for k in
                   ("j", "d", "lib", "code", "inC", "outC", "inU", "outU"))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def matches_contract(sheet_names: List[str]) -> bool:
    """Both journals present = the workbook speaks this contract."""
    names = {str(n).strip() for n in sheet_names}
    return SITE_SHEET in names and DGO_SHEET in names


# ---------------------------------------------------------------- [1] parse
def parse_journals(sheets: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    """Both cash journals -> normalized transaction dicts (spec section 2).
    Receives the already-ingested raw grids (header-less DataFrames)."""
    spec = {
        SITE_SHEET: ("site", "Référence Liée", "Personne/Service"),
        DGO_SHEET: ("dgo", "N°PCS", "CPT"),
    }
    rows: List[Dict[str, Any]] = []
    for sheet, (tag, ref_col, party_col) in spec.items():
        grid = sheets.get(sheet)
        if grid is None or grid.shape[0] <= HEADER_ROW + 1:
            continue
        header = [_clean(c) for c in grid.iloc[HEADER_ROW]]
        df = grid.iloc[HEADER_ROW + 1:].copy()
        df.columns = [h if h else f"_c{i}" for i, h in enumerate(header)] \
            + [f"_x{i}" for i in range(len(df.columns) - len(header))]
        col = {c: c for c in df.columns}
        if "Dates" not in col:
            continue
        df = df[df["Dates"].notna()]
        for seq, (_, r) in enumerate(df.iterrows()):
            d = pd.to_datetime(r["Dates"], errors="coerce")
            if pd.isna(d):
                continue
            t = {
                "j": tag, "seq": seq, "d": d.strftime("%Y-%m-%d"),
                "m": d.strftime("%Y-%m"),
                "ref": _clean(r.get(ref_col)), "who": _clean(r.get(party_col)),
                "lib": _clean(r.get("Libellés")),
                "code": _clean(r.get("Code Interne")),
                "gl": _clean(r.get("CODE")),
                "inC": _num(r.get("Entrées CDF")),
                "outC": _num(r.get("Sorties CDF")),
                "inU": _num(r.get("Entrées USD")),
                "outU": _num(r.get("Sorties USD")),
                "balC": _num(r.get("Solde CDF")),
                "balU": _num(r.get("Solde USD")),
            }
            t["hash"] = _row_hash(t)
            rows.append(t)
    rows.sort(key=lambda x: (x["d"], x["j"], x["seq"]))
    return rows


_SECTION_KIND = (
    ("capex", re.compile(r"capex|invest|replant", re.IGNORECASE)),
    ("overhead", re.compile(r"dgo|head\s*office|si[eè]ge", re.IGNORECASE)),
    ("opex", re.compile(r"opex|admin|plantation|mill|technique",
                        re.IGNORECASE)),
)


def parse_categories(sheets: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    """Glossary codes + sections from the EXTRACTION sheet layout, detected
    dynamically: a row with text in the label column but no code is a
    section header; coded rows below belong to it. Survives row drift that
    would break fixed ranges."""
    grid = sheets.get(EXTRACTION_SHEET)
    if grid is None:
        return []
    cats: List[Dict[str, Any]] = []
    section, kind = "", "opex"
    seen: set = set()
    for _, row in grid.iterrows():
        label = _clean(row.iloc[1]) if len(row) > 1 else ""
        code = _clean(row.iloc[2]) if len(row) > 2 else ""
        if label and not code:
            section = label
            kind = next((k for k, rx in _SECTION_KIND if rx.search(label)),
                        "opex")
            continue
        if code and code not in seen:
            seen.add(code)
            cats.append({"code": code, "label": label, "sec": section,
                         "kind": kind,
                         "in_cost": code not in NON_COST_CODES})
    return cats


def parse_pivot(sheets: Dict[str, pd.DataFrame]) -> float:
    """The fixed CDF/USD pivot, from the extraction sheet (cell E4 in the
    reference layout — scanned, not hardcoded to the cell)."""
    grid = sheets.get(EXTRACTION_SHEET)
    if grid is not None:
        for _, row in grid.head(6).iterrows():
            for v in row:
                n = _num(v)
                if 500 <= n <= 20000:      # a plausible CDF/USD rate
                    return n
    return PIVOT_DEFAULT


# ---------------------------------------------------------------- [3] validate
def validate(rows: List[Dict[str, Any]], cats: List[Dict[str, Any]],
             pivot: float) -> List[Dict[str, Any]]:
    known = {c["code"] for c in cats} | {"REPORT"}
    non_cost = {c["code"] for c in cats if not c["in_cost"]} | NON_COST_CODES
    out: List[Dict[str, Any]] = []
    site = [r for r in rows if r["j"] == "site"]
    dgo = [r for r in rows if r["j"] == "dgo" and r["outU"] > 0]

    def f(rule: str, sev: str, d: str, code: str, lib: str, usd: float,
          msg: str, msg_fr: str) -> None:
        out.append({"rule": rule, "sev": sev, "d": d, "code": code,
                    "lib": lib, "usd": round(usd, 2),
                    "msg": msg, "msg_fr": msg_fr})

    # V1 orphan-dgo: DGO outflow with no same-month site row at the amount
    used: set = set()
    for r in dgo:
        hit = next(
            (i for i, s in enumerate(site) if i not in used
             and s["m"] == r["m"]
             and (abs(s["outU"] - r["outU"]) < V1_TOL_USD
                  or abs(s["outC"] - r["outU"] * pivot) < V1_TOL_CDF)),
            None)
        if hit is None:
            f("V1", "HIGH", r["d"], r["code"], r["lib"], r["outU"],
              "no matching consolidated entry",
              "aucune écriture correspondante au journal consolidé")
        else:
            used.add(hit)
    # V2 zero-amount rows
    for r in rows:
        if r["lib"] and r["code"] != "REPORT" and not any(
                (r["inC"], r["outC"], r["inU"], r["outU"])):
            f("V2", "HIGH", r["d"], r["code"], r["lib"], 0,
              "row recorded with amount = 0",
              "ligne enregistrée avec un montant = 0")
    # V3 transfer-in-cost
    for r in site:
        if r["code"] in non_cost and r["outC"] > 0:
            f("V3", "HIGH", r["d"], r["code"], r["lib"], r["outC"] / pivot,
              "transfer/neutral code counted as spend",
              "code de transfert/neutre compté comme dépense")
    # V4 unknown codes (trim + case-fold before comparing)
    fold = {k.casefold() for k in known}
    for code in sorted({r["code"] for r in rows}):
        if code and code.casefold() not in fold:
            usd = sum(r["outU"] or r["outC"] / pivot
                      for r in rows if r["code"] == code)
            f("V4", "MED", "", code, "", usd,
              "code not in glossary", "code absent du glossaire")
    # V5 date order (original sheet order — Excel SUMIF ranges break on it)
    for tag in ("site", "dgo"):
        js = sorted((r for r in rows if r["j"] == tag),
                    key=lambda x: x["seq"])
        for prev, cur in zip(js, js[1:]):
            if cur["d"] < prev["d"]:
                f("V5", "MED", cur["d"], cur["code"], cur["lib"], 0,
                  f"row out of date order in {tag} journal "
                  f"(after {prev['d']})",
                  f"ligne hors ordre chronologique dans le journal {tag} "
                  f"(après {prev['d']})")
    # V6 balance continuity — original order, per journal & currency
    for tag, bal, inn, outc in (("site", "balC", "inC", "outC"),
                                ("site", "balU", "inU", "outU"),
                                ("dgo", "balU", "inU", "outU")):
        js = sorted((r for r in rows if r["j"] == tag),
                    key=lambda x: x["seq"])
        for prev, cur in zip(js, js[1:]):
            if abs(cur[bal] - (prev[bal] + cur[inn] - cur[outc])) > 1:
                f("V6", "MED", cur["d"], cur["code"], cur["lib"], 0,
                  f"balance break ({tag}/{bal})",
                  f"rupture de solde ({tag}/{bal})")
    # V8 duplicates: same journal + date + amounts + libellé, 2+ times
    seen: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not (r["outC"] or r["outU"] or r["inC"] or r["inU"]):
            continue
        if r["hash"] in seen:
            f("V8", "LOW", r["d"], r["code"], r["lib"],
              r["outU"] or r["outC"] / pivot,
              "possible duplicate entry (same date, amounts, label)",
              "doublon possible (même date, montants, libellé)")
        else:
            seen[r["hash"]] = r
    return out


# ---------------------------------------------------------------- [4] aggregate
def aggregate(rows: List[Dict[str, Any]], cats: List[Dict[str, Any]],
              months: List[str], pivot: float) -> List[Dict[str, Any]]:
    """Date-driven SUM(out_cdf) by code × month — replaces the Excel SUMIF
    ranges. Codes absent from the glossary land in an explicit UNMAPPED
    bucket so the tie-out holds and the gap stays visible."""
    site = [r for r in rows if r["j"] == "site"]
    known_fold = {c["code"].casefold() for c in cats}
    agg = []
    for c in cats:
        cdf = [round(sum(r["outC"] for r in site
                         if r["code"].casefold() == c["code"].casefold()
                         and r["m"] == m), 2) for m in months]
        agg.append({**c, "cdf": cdf,
                    "usd": [round(v / pivot, 2) for v in cdf]})
    un = [round(sum(r["outC"] for r in site
                    if r["code"].casefold() not in known_fold
                    and r["m"] == m), 2) for m in months]
    if any(un):
        agg.append({"code": UNMAPPED, "label": "codes hors glossaire",
                    "sec": UNMAPPED, "kind": "unmapped", "in_cost": False,
                    "cdf": un, "usd": [round(v / pivot, 2) for v in un]})
    return agg


# ---------------------------------------------------------------- [5] payload
def build_journal_block(
        sheets: Dict[str, pd.DataFrame]) -> Optional[Dict[str, Any]]:
    """The full journal audit for a contract-matching workbook, or None."""
    if not matches_contract(list(sheets.keys())):
        return None
    rows = parse_journals(sheets)
    if not rows:
        return None
    cats = parse_categories(sheets)
    pivot = parse_pivot(sheets)
    months = sorted({r["m"] for r in rows})
    findings = validate(rows, cats, pivot)
    agg = aggregate(rows, cats, months, pivot)

    # V7 tie-out: aggregation must equal the journal, exactly — a break is
    # a BLOCKER finding, never an exception
    total_cats = round(sum(sum(c["cdf"]) for c in agg), 2)
    total_journal = round(sum(r["outC"] for r in rows if r["j"] == "site"), 2)
    if abs(total_cats - total_journal) > 1:
        findings.append({
            "rule": "V7", "sev": "BLOCKER", "d": "", "code": "", "lib": "",
            "usd": round((total_cats - total_journal) / pivot, 2),
            "msg": f"tie-out failed: categories {total_cats:,.0f} CDF vs "
                   f"journal {total_journal:,.0f} CDF",
            "msg_fr": f"rapprochement rompu : catégories {total_cats:,.0f} "
                      f"CDF contre journal {total_journal:,.0f} CDF"})

    sev_rank = {"BLOCKER": 0, "HIGH": 1, "MED": 2, "LOW": 3}
    findings.sort(key=lambda x: (sev_rank.get(x["sev"], 9), -abs(x["usd"])))
    dgo_out = round(sum(r["outU"] for r in rows if r["j"] == "dgo"), 2)

    # exec voice: destinations and exceptions as aggregates — the ONLY part
    # of the journal audit that travels to published/portal surfaces. Shares
    # are of ALL cash out (site converted at the pivot + the DGO journal),
    # the reference deliverable's own convention.
    site_usd = round(total_journal / pivot, 2)
    all_out = round(site_usd + dgo_out, 2)
    dests = []
    for kind in ("opex", "capex", "overhead", "unmapped"):
        cdf_t = round(sum(sum(c["cdf"]) for c in agg if c["kind"] == kind), 2)
        if not cdf_t:
            continue
        usd_t = round(cdf_t / pivot, 2)
        dests.append({"kind": kind, "usd": usd_t,
                      "pct": round(usd_t / all_out * 100, 1)
                      if all_out else None})
    if dgo_out:
        dests.append({"kind": "dgo", "usd": dgo_out,
                      "pct": round(dgo_out / all_out * 100, 1)
                      if all_out else None})
    dests.sort(key=lambda x: -x["usd"])
    exec_block = {
        "months": months, "rate": pivot,
        "site_out_cdf": total_journal, "site_out_usd": site_usd,
        "total_out_usd": all_out,
        "destinations": dests,
        "exceptions": {
            "n": len(findings),
            "n_high": sum(1 for x in findings
                          if x["sev"] in ("HIGH", "BLOCKER")),
            "at_stake_usd": round(sum(abs(x["usd"]) for x in findings), 2),
        },
    }
    return {
        "exec": exec_block,
        "months": months,
        "cats": agg,
        "findings": findings,
        "meta": {
            "rate": pivot,
            "tie_out_cdf": total_journal,
            "n_rows": len(rows),
            "n_site_rows": sum(1 for r in rows if r["j"] == "site"),
            "n_dgo_rows": sum(1 for r in rows if r["j"] == "dgo"),
            "dgo_out_usd": dgo_out,
            "n_findings": len(findings),
            "n_high": sum(1 for x in findings
                          if x["sev"] in ("HIGH", "BLOCKER")),
        },
    }
