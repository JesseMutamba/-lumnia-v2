"""The composed operations report: a client deliverable assembled from the
pipeline's computed blocks — KPI strip, plan vs actual, conversion rate,
cash split, monthly detail table.

Deterministic figures end to end: every number comes from the stored model
(itself computed over the uploaded workbook), no scripts, no external
fetches — charts are hand-built inline SVG in Lumnia's design language.
The one AI element is the optional ``narrative`` block, which reprints the
STORED narrative verbatim (Claude phrased it from the pipeline's verified
figures at generation time; nothing is generated or recomputed here). A
block the data cannot support is not rendered; the endpoint declares it
as skipped instead.
"""
from __future__ import annotations

import base64 as _b64
import datetime as _dt
import functools as _ft
import html as _html
import re as _re
from pathlib import Path as _Path
from typing import Any, Dict, List, Optional

# selectable blocks, in reading order
BLOCKS = ("narrative", "dashboard", "kpi", "plan_progress",
          "plan_vs_actual", "unit_cost", "conversion", "cash", "outlook",
          "net_cash", "breakdowns", "monthly_table")

GOLD, PALE, INK3 = "#a8821f", "#d5c391", "#99917e"
CRIT, WARN, GOOD = "#a33b32", "#a07d1c", "#3e6f4e"

# The deliverable must render brand-correct OFFLINE — no external fetches —
# so the brand fonts travel inside the file as data URIs (~95KB total,
# OFL-licensed latin subsets; licenses alongside the files).
_FONT_DIR = _Path(__file__).parent / "static" / "fonts"
_FACES = (
    ("Source Serif 4", 400, "source-serif-4-latin-400-normal.woff2"),
    ("Source Serif 4", 600, "source-serif-4-latin-600-normal.woff2"),
    ("IBM Plex Mono", 400, "ibm-plex-mono-latin-400-normal.woff2"),
    ("IBM Plex Mono", 500, "ibm-plex-mono-latin-500-normal.woff2"),
)


@_ft.lru_cache(maxsize=1)
def _font_css() -> str:
    rules = []
    for fam, weight, fname in _FACES:
        b64 = _b64.b64encode((_FONT_DIR / fname).read_bytes()).decode()
        rules.append(
            f"@font-face {{ font-family:'{fam}'; font-style:normal; "
            f"font-weight:{weight}; font-display:swap; "
            f"src:url(data:font/woff2;base64,{b64}) format('woff2'); }}")
    return "\n".join(rules)


def _blk(title: str, body: str, cls: str = "blk") -> str:
    """One report section as a native <details> fold, OPEN by default:
    nothing is hidden from the reader, folding is a reading aid — and an
    open fold prints. No scripts involved."""
    return (f'<details class="{cls}" open><summary><h2>'
            f'{_html.escape(title)}</h2></summary>{body}</details>')

_ROLES = {
    "en": {"volume": "Volume", "volume_secondary": "Output",
           "opex": "Operating costs", "capex": "CAPEX",
           "revenue": "Revenue", "budget": "Budget",
           "area": "Area", "headcount": "Headcount", "price": "Price"},
    "fr": {"volume": "Volume", "volume_secondary": "Production",
           "opex": "Charges d'exploitation", "capex": "CAPEX",
           "revenue": "Revenus", "budget": "Budget",
           "area": "Superficie", "headcount": "Effectifs", "price": "Prix"},
}

_STR = {
    "en": {
        "kicker": "Operations report",
        "title": lambda stem: f"Operations report — {stem}",
        "window": lambda a, b, n: f"{a} – {b} · {n} months",
        "of_plan_tile": lambda r: f"{r} — % of plan",
        "total_tile": lambda l: f"Total {l}",
        "narr_t": "Executive narrative",
        "narr_foot": ("AI-phrased from the audit's verified figures — the "
                      "pipeline computed every number; the narrative only "
                      "puts them in words."),
        "dash_t": lambda a, b: f"Financial trajectory {a}–{b}",
        "dash_t_plan": lambda a, b: f"Projection trajectory {a}–{b}",
        "dash_total": lambda a, b: f"{a}–{b} total",
        "dash_cap": ("Yearly totals as the model extracted them — revenue "
                     "against operating and investment spend."),
        "dash_cov": lambda k, n: f" {k} of {n} periods carry data.",
        "pp_t": lambda y: f"Progress vs plan {y}",
        "pp_line": lambda a, p, exp, ph: (
            f"{a} of {p} planned · expected {exp} ({ph})"),
        "pp_phasing": {"linear": "linear pace",
                       "monthly_plan": "phased by the monthly plan"},
        "pp_journal": "verified journal",
        "pp_over": "over pace", "pp_behind": "behind plan",
        "pp_on": "on pace",
        "pp_needs": lambda r: f"needs: {r}",
        "pva_t": lambda r: f"Plan vs actual — {r}",
        "pva_cap": lambda pct, n, al, pl: (
            f"{pct} of plan over {n} aligned month(s) — actual “{al}” "
            f"against plan “{pl}”."),
        "conv_t": "Conversion rate by period",
        "conv_cap": lambda avg, n: f"average {avg} over {n} month(s).",
        "cash_t": "Where the tracked spend went",
        "cash_cap": lambda pct: (
            f"{pct} of the tracked spend was invested (CAPEX), the rest "
            "operated the site — counted only over months where both "
            "sides are tracked."),
        "tbl_t": "Monthly detail",
        "tbl_omitted": lambda n: (
            f"{n} month{'' if n == 1 else 's'} with nothing tracked "
            "omitted from this table."),
        "period": "Period", "actual": "Actual", "plan": "Plan",
        "pct": "% of plan",
        "opex_lbl": "Operating", "capex_lbl": "Invested",
        "unit_cost_t": "Cost per tonne",
        "blended_sub": "blended over the tracked window",
        "vs_budget_sub": lambda t, r: f"vs {t} budget — {r}×",
        "vs_plan_sub": lambda p, pc: f"vs {p} planned — {pc} attainment",
        "vs_plan_spend_sub": lambda p, pc, w: f"vs {p} planned — {pc} · {w}",
        "under": "under plan", "over": "over plan",
        "invested_t": "Invested share",
        "invested_sub": "CAPEX share of tracked spend",
        "conv_t2": "Conversion rate",
        "conv_sub": lambda n: f"average over {n} month(s)",
        "uc_t": "Cost per tonne — actual vs references",
        "uc_cap": lambda v: f"latest actual {v} per tonne.",
        "ref_budget": lambda y: f"budget {y}",
        "ref_phased": "phased plan",
        "outlook_t": "Full plan vs the run-rate so far",
        "ref_required": "required/month",
        "outlook_cap": lambda tot, req, n, avg: (
            f"To still reach the {tot} plan, the remaining {n} month(s) "
            f"need {req}/month (plan average: {avg})."),
        "jx_t": "Where the cash actually went",
        "jx_cap": lambda n, tot: (
            f"All cash out over {n} month(s): {tot} USD — site journal "
            "converted at the fixed rate, plus the DGO journal."),
        "jx_exc": lambda n, usd: (
            f"{n} journal entr{'y' if n == 1 else 'ies'} still need "
            f"clarification ({usd} USD at stake)."),
        "net_t": "The plan being defended — net balance",
        "breakeven": lambda p: f"breakeven {p}",
        "bridge": lambda v: f"capital bridge {v}",
        "bd_t": "Largest line items",
        "audit_line": lambda ok, bad, un: (
            f"Audit: {ok} relation(s) verified · {bad} flagged · "
            f"{un} unverified"),
        "footer": ("Generated by Lumnia from the audited workbook — every "
                   "figure on this page traces to deterministic computation "
                   "over the uploaded data."),
    },
    "fr": {
        "kicker": "Rapport d'exploitation",
        "title": lambda stem: f"Rapport d'exploitation — {stem}",
        "window": lambda a, b, n: f"{a} – {b} · {n} mois",
        "of_plan_tile": lambda r: f"{r} — % du plan",
        "total_tile": lambda l: f"Total {l}",
        "narr_t": "Synthèse rédigée",
        "narr_foot": ("Rédigée par IA à partir des chiffres vérifiés de "
                      "l'audit — le pipeline a calculé chaque nombre ; la "
                      "synthèse ne fait que les mettre en mots."),
        "dash_t": lambda a, b: f"Trajectoire financière {a}–{b}",
        "dash_t_plan": lambda a, b: f"Trajectoire des projections {a}–{b}",
        "dash_total": lambda a, b: f"total {a}–{b}",
        "dash_cap": ("Totaux annuels tels qu'extraits par le modèle — "
                     "revenus contre dépenses d'exploitation et "
                     "d'investissement."),
        "dash_cov": lambda k, n: f" {k} périodes sur {n} renseignées.",
        "pp_t": lambda y: f"Avancement vs plan {y}",
        "pp_line": lambda a, p, exp, ph: (
            f"{a} sur {p} planifiés · attendu {exp} ({ph})"),
        "pp_phasing": {"linear": "rythme linéaire",
                       "monthly_plan": "phasé par le plan mensuel"},
        "pp_journal": "journal vérifié",
        "pp_over": "au-dessus du rythme", "pp_behind": "en retard sur le plan",
        "pp_on": "dans le rythme",
        "pp_needs": lambda r: f"requiert : {r}",
        "pva_t": lambda r: f"Plan vs réel — {r}",
        "pva_cap": lambda pct, n, al, pl: (
            f"{pct} du plan sur {n} mois alignés — réel « {al} » "
            f"contre plan « {pl} »."),
        "conv_t": "Taux de conversion par période",
        "conv_cap": lambda avg, n: f"moyenne {avg} sur {n} mois.",
        "cash_t": "Où est allée la dépense suivie",
        "cash_cap": lambda pct: (
            f"{pct} de la dépense suivie a été investie (CAPEX), le reste "
            "a fait tourner le site — compté uniquement sur les mois où "
            "les deux volets sont suivis."),
        "tbl_t": "Détail mensuel",
        "tbl_omitted": lambda n: (
            f"{n} mois sans aucun suivi omis de ce tableau."),
        "period": "Période", "actual": "Réel", "plan": "Plan",
        "pct": "% du plan",
        "opex_lbl": "Exploitation", "capex_lbl": "Investi",
        "unit_cost_t": "Coût par tonne",
        "blended_sub": "moyenne sur la fenêtre suivie",
        "vs_budget_sub": lambda t, r: f"contre budget {t} — {r}×",
        "vs_plan_sub": lambda p, pc: f"contre {p} au plan — {pc} atteints",
        "vs_plan_spend_sub": lambda p, pc, w: f"contre {p} au plan — {pc} · {w}",
        "under": "sous le plan", "over": "au-dessus du plan",
        "invested_t": "Part investie",
        "invested_sub": "part CAPEX de la dépense suivie",
        "conv_t2": "Taux de conversion",
        "conv_sub": lambda n: f"moyenne sur {n} mois",
        "uc_t": "Coût par tonne — réel contre références",
        "uc_cap": lambda v: f"dernier réel {v} par tonne.",
        "ref_budget": lambda y: f"budget {y}",
        "ref_phased": "plan phasé",
        "outlook_t": "Plan complet contre le rythme constaté",
        "ref_required": "requis/mois",
        "outlook_cap": lambda tot, req, n, avg: (
            f"Pour atteindre le plan de {tot}, les {n} mois restants "
            f"exigent {req}/mois (moyenne du plan : {avg})."),
        "jx_t": "Où est allé l'argent réellement",
        "jx_cap": lambda n, tot: (
            f"Sorties totales sur {n} mois : {tot} USD — journal du site "
            "converti au taux fixe, plus le journal DGO."),
        "jx_exc": lambda n, usd: (
            f"{n} écriture{'' if n == 1 else 's'} de journal "
            f"{'demande' if n == 1 else 'demandent'} encore une "
            f"clarification ({usd} USD en jeu)."),
        "net_t": "Le plan défendu — solde net",
        "breakeven": lambda p: f"équilibre {p}",
        "bridge": lambda v: f"besoin de financement {v}",
        "bd_t": "Principaux postes",
        "audit_line": lambda ok, bad, un: (
            f"Audit : {ok} relation(s) vérifiée(s) · {bad} signalée(s) · "
            f"{un} non vérifiée(s)"),
        "footer": ("Généré par Lumnia à partir du classeur audité — chaque "
                   "chiffre de cette page provient d'un calcul déterministe "
                   "sur les données déposées."),
    },
}


# month abbreviations, deterministic — no locale machinery
_MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin",
           "juil.", "août", "sept.", "oct.", "nov.", "déc."],
}

_PERIOD_RX = _re.compile(r"^(\d{4})-(\d{2})")


def _period(p: str, lang: str) -> str:
    """'2026-01-28' reads 'janv. 2026' / 'Jan 2026'; anything that isn't a
    year-month string passes through untouched."""
    m = _PERIOD_RX.match(str(p))
    if not m:
        return str(p)
    month = int(m.group(2))
    if not 1 <= month <= 12:
        return str(p)
    return f"{_MONTHS[lang][month - 1]} {m.group(1)}"


def _fmt(v: Optional[float], lang: str, dec: Optional[int] = None) -> str:
    if v is None:
        return "—"
    if dec is None:
        dec = 1 if abs(v) < 100 and v != int(v) else 0
    s = f"{v:,.{dec}f}"
    if lang == "fr":                    # 1 234,5 — narrow nbsp thousands
        s = s.replace(",", " ").replace(".", ",")
    return s


def _pct(v: Optional[float], lang: str) -> str:
    if v is None:
        return "—"
    return _fmt(v, lang, 1) + (" %" if lang == "fr" else "%")


def _monthly(report: Dict[str, Any]) -> Dict[str, Any]:
    return (report.get("model") or {}).get("monthly") or {}


def _vol_role(pva: Dict[str, Any]) -> Optional[str]:
    """The volume-flavored plan-vs-actual pair, output first."""
    for role in ("volume_secondary", "volume"):
        if role in pva:
            return role
    return None


def available_blocks(report: Dict[str, Any]) -> List[str]:
    """Blocks the stored model can actually support — the honest menu."""
    model = report.get("model") or {}
    mo = _monthly(report)
    met = mo.get("metrics") or {}
    der = mo.get("derived") or {}
    pva = mo.get("plan_vs_actual") or {}
    out: List[str] = []
    if (report.get("narrative") or {}).get("narrative"):
        out.append("narrative")
    if any((m.get("values") or []) for m in (model.get("metrics")
                                             or {}).values()):
        out.append("dashboard")
    if met:
        out.append("kpi")
    pp = model.get("plan_progress") or {}
    if pp.get("roles") or pp.get("gaps"):
        out.append("plan_progress")
    if pva:
        out.append("plan_vs_actual")
    if any(v is not None
           for b in ("opex_per_volume_out", "opex_per_volume")
           for v in der.get(b) or []):
        out.append("unit_cost")
    if any(v is not None for v in der.get("volume_ratio") or []):
        out.append("conversion")
    if mo.get("cash") or ((report.get("journal") or {}).get("exec") or {}
                          ).get("destinations"):
        out.append("cash")
    plan = mo.get("plan") or {}
    if (_vol_role(pva) and plan.get("periods")
            and len(plan["periods"]) > len(mo.get("periods") or [])):
        out.append("outlook")
    if any(v is not None
           for v in (model.get("derived") or {}).get("net_cash") or []):
        out.append("net_cash")
    if model.get("breakdowns"):
        out.append("breakdowns")
    if met:
        out.append("monthly_table")
    return out


def _compact(v: float, lang: str) -> str:
    """Short value labels for bars: 5227670 -> 5.23M, 870110 -> 870K,
    4744 -> 4,744, 2.4 -> 2.4. Signed; deterministic."""
    a = abs(v)
    if a >= 1e6:
        s = f"{v / 1e6:.2f}".rstrip("0").rstrip(".") + "M"
    elif a >= 10_000:
        s = f"{v / 1e3:.0f}K"
    else:
        s = _fmt(v, lang)
    return s.replace(".", ",") if lang == "fr" and "M" in s else s


def _svg_bars(periods: List[str], series: List[tuple], lang: str,
              w: int = 640, h: int = 200,
              refs: List[tuple] = (), colors: Optional[List] = None,
              neg_ok: bool = False,
              names: Optional[List[str]] = None) -> str:
    """Grouped bars, one group per period; series = (values, color). None
    cells simply have no bar — absence is drawn as absence. ``refs`` draws
    dashed horizontal reference lines (label, value, color); ``colors``
    overrides the fill per cell for a single series; ``neg_ok`` keeps a
    zero baseline mid-chart so negative bars hang below it.

    This is a DOCUMENT chart, not a screen widget: every bar carries an
    always-visible value label (horizontal when few bars, rotated when
    dense), the y scale shows zero/mid/max gridlines, and ``names`` render
    as a legend above the plot. Hover tooltips (<title> + the .tt readout)
    stay as a bonus for screens, never as the only path to a number.
    Values are formatted here, at compose time; nothing recomputes in the
    browser and no script is involved."""
    n = max(1, len(periods))
    n_bars = n * len(series)
    rotated = n_bars > 10
    ml, mr, mt, mb = 8, 8, (46 if rotated else 22), 22
    pw, ph = w - ml - mr, h - mt - mb
    allv = [v for vals, _ in series for v in vals if v is not None]
    refv = [r[1] for r in refs if r[1] is not None]
    mx = max([abs(v) for v in allv + refv], default=0)
    if mx == 0:
        return ""
    lo = min(allv + [0]) if neg_ok else 0
    span = mx - lo if mx > lo else mx
    y_of = lambda v: mt + ph - (v - lo) / span * ph  # noqa: E731
    y0 = y_of(0)
    group = pw / n
    bw = group * 0.72 / len(series)
    bars, labels = [], []
    for i, p in enumerate(periods):
        x0 = ml + i * group + group * 0.14
        for k, (vals, color) in enumerate(series):
            v = vals[i] if i < len(vals) else None
            if v is None:
                continue
            fill = (colors[i] if colors and i < len(colors) and colors[i]
                    else color)
            top = min(y_of(v), y0)
            name = names[k] if names and k < len(names) else None
            read = (f"{_period(p, lang)}"
                    + (f" · {name}" if name else "")
                    + f" · {_fmt(v, lang)}")
            xc = x0 + k * bw + bw / 2
            # the value, printed ON the page — hover is a bonus, never
            # the only path to a number (print/PDF/mobile carry no hover)
            cval = _html.escape(_compact(v, lang))
            if rotated:
                vy = top - 3 if v >= 0 else min(y_of(v) + 3, h - mb - 2)
                anchor = "start" if v >= 0 else "end"
                vlabel = (f'<text class="bv2" transform="rotate(-90 '
                          f'{xc + 2.5:.1f} {vy:.1f})" x="{xc + 2.5:.1f}" '
                          f'y="{vy:.1f}" text-anchor="{anchor}">{cval}</text>')
            else:
                vy = max(top - 4, mt + 8) if v >= 0 \
                    else min(y_of(v) + 11, h - mb - 3)
                vlabel = (f'<text class="bv2" x="{xc:.1f}" y="{vy:.1f}" '
                          f'text-anchor="middle">{cval}</text>')
            bars.append(
                f'<g class="b"><title>{_html.escape(read)}</title>'
                f'<rect x="{x0 + k * bw:.1f}" y="{top:.1f}" '
                f'width="{bw:.1f}" height="{abs(y_of(v) - y0):.1f}" '
                f'fill="{fill}"/>{vlabel}'
                f'<text class="tt" x="{w - mr}" y="12" text-anchor="end">'
                f'{_html.escape(read)}</text></g>')
        step = 1 if n <= 8 else 2 if n <= 16 else 3
        if i % step == 0:
            labels.append(
                f'<text x="{ml + i * group + group / 2:.1f}" y="{h - 6}" '
                f'text-anchor="middle" class="ax">'
                f'{_html.escape(_period(p, lang))}</text>')
    ref_svg = "".join(
        f'<line x1="{ml}" y1="{y_of(rv):.1f}" x2="{w - mr}" y2="{y_of(rv):.1f}" '
        f'stroke="{rc}" stroke-dasharray="5 4" stroke-width="1.3"/>'
        f'<text x="{w - mr}" y="{y_of(rv) - 4:.1f}" text-anchor="end" '
        f'class="ax" fill="{rc}">{_html.escape(rl)} '
        f'{_html.escape(_fmt(rv, lang))}</text>'
        for rl, rv, rc in refs if rv is not None)
    # y scale a reader can use: zero baseline + mid + max gridlines,
    # each labelled (halo keeps them legible over bars)
    gl = []
    for gv in sorted({0.0, lo + span * 0.5, float(mx)}):
        yv = y_of(gv)
        gl.append(f'<line x1="{ml}" y1="{yv:.1f}" x2="{w - mr}" '
                  f'y2="{yv:.1f}" stroke="#ddd4bf" '
                  f'stroke-width="{1.4 if gv == 0 else 0.7}"/>')
        gl.append(f'<text x="{ml + 2}" y="{yv - 3:.1f}" class="ax gv">'
                  f'{_html.escape(_compact(gv, lang))}</text>')
    legend = ""
    if names:
        legend = '<div class="lg">' + "".join(
            f'<span><span class="sw" style="background:{c}"></span>'
            f'{_html.escape(str(nm))}</span>'
            for nm, (_, c) in zip(names, series)) + "</div>"
    return (legend + f'<svg viewBox="0 0 {w} {h}" role="img">'
            f"{''.join(gl)}{''.join(bars)}{ref_svg}{''.join(labels)}</svg>")


def _blended_unit_cost(mo: Dict[str, Any]) -> Optional[float]:
    """Total opex over total output across months where BOTH are tracked —
    the quarter's blended $/t, same apples-to-apples rule as _cash_split."""
    met = mo.get("metrics") or {}
    opx = (met.get("opex") or {}).get("values") or []
    vol = (met.get("volume_secondary") or met.get("volume") or {}
           ).get("values") or []
    both = [(o, v) for o, v in zip(opx, vol)
            if o is not None and v is not None and v != 0]
    if not both:
        return None
    total_v = sum(v for _, v in both)
    return round(sum(o for o, _ in both) / total_v, 2) if total_v else None


def _tiles(mo: Dict[str, Any], lang: str) -> List[tuple]:
    """(label, value, sub, state) — states are rule-based and declared:
    volume attainment <70% bad / <100% warn; spend over plan warn, >120%
    bad; unit cost >1.25x budget bad, over budget warn."""
    s, roles = _STR[lang], _ROLES[lang]
    met = mo.get("metrics") or {}
    pva = mo.get("plan_vs_actual") or {}
    tiles: List[tuple] = []

    blended = _blended_unit_cost(mo)
    ucb = mo.get("unit_cost_budget")
    if blended is not None:
        sub, state = s["blended_sub"], ""
        if ucb and ucb.get("target"):
            ratio = blended / ucb["target"]
            sub = s["vs_budget_sub"](_fmt(ucb["target"], lang),
                                     _fmt(round(ratio, 1), lang, 1))
            state = "bad" if ratio > 1.25 else "warn" if ratio > 1 else "good"
        tiles.append((s["unit_cost_t"], _fmt(blended, lang), sub, state))

    vrole = _vol_role(pva)
    if vrole and pva[vrole].get("pct_of_plan_total") is not None:
        v = pva[vrole]
        pct = v["pct_of_plan_total"]
        tiles.append((
            str((met.get(vrole) or {}).get("label", roles[vrole]))[:26],
            _fmt(v["actual_total"], lang),
            s["vs_plan_sub"](_fmt(v["plan_total"], lang), _pct(pct, lang)),
            "bad" if pct < 70 else "warn" if pct < 100 else "good"))

    if "opex" in pva and pva["opex"].get("pct_of_plan_total") is not None:
        v = pva["opex"]
        pct = v["pct_of_plan_total"]
        tiles.append((
            roles["opex"], _fmt(v["actual_total"], lang),
            s["vs_plan_spend_sub"](_fmt(v["plan_total"], lang),
                                   _pct(pct, lang),
                                   s["under"] if pct <= 100 else s["over"]),
            "good" if pct <= 100 else "warn" if pct <= 120 else "bad"))
    elif met.get("opex"):
        m = met["opex"]
        tiles.append((s["total_tile"](str(m["label"])[:24]),
                      _fmt(sum(v for v in m["values"] if v is not None),
                           lang), "", ""))

    cash = mo.get("cash")
    if cash:
        tiles.append((s["invested_t"], _pct(cash["invested_pct"], lang),
                      s["invested_sub"], ""))

    vr = (mo.get("derived") or {}).get("volume_ratio") or []
    known = [v for v in vr if v is not None]
    if known:
        tiles.append((s["conv_t2"],
                      _pct(round(sum(known) / len(known) * 100, 1), lang),
                      s["conv_sub"](len(known)), ""))
    return tiles[:5]


def _narrative_section(report: Dict[str, Any], lang: str) -> str:
    """The stored AI narrative, reprinted verbatim with its provenance
    stated. Nothing is generated here: no narrative stored -> no block."""
    s = _STR[lang]
    n = report.get("narrative") or {}
    if not n.get("narrative"):
        return ""
    paras = "".join(f"<p>{_html.escape(p.strip())}</p>"
                    for p in str(n["narrative"]).split("\n") if p.strip())
    watch = "".join(f"<li>{_html.escape(str(w))}</li>"
                    for w in (n.get("watchouts") or [])[:3])
    body = (f'<p class="nhead">{_html.escape(str(n.get("headline", "")))}</p>'
            f'{paras}'
            + (f'<ul class="nwatch">{watch}</ul>' if watch else "")
            + f'<p class="nfoot">{_html.escape(s["narr_foot"])}</p>')
    return _blk(s["narr_t"], body, cls="blk wide")


def _dashboard_section(model: Dict[str, Any], lang: str) -> str:
    """Year-trajectory dashboard from the business model: one tile per
    role (series total over the model years, plus the latest margin), and
    revenue vs opex vs capex as grouped bars. Totals are sums of the
    model's own series — aggregation of computed values, nothing new.

    # DEBT: the pipeline doesn't model series units (currency vs tonnes vs
    # headcount), so tiles and charts can't print unit suffixes — money and
    # volume sit unlabelled side by side. Needs a `unit` field on metrics,
    # extracted from labels/headers, before any suffix can be honest.
    # DEBT: all block headers share one type size — no visual hierarchy
    # between decision blocks (plan progress) and reference blocks
    # (monthly table); needs a deliberate type ramp, not an ad-hoc bump."""
    from .pipeline.model import BUDGET_SHEET_RX
    s, roles = _STR[lang], _ROLES[lang]
    model = model or {}
    met = model.get("metrics") or {}
    periods = [str(p) for p in model.get("periods") or []]
    if not periods or not any((m.get("values") or []) for m in met.values()):
        return ""
    a, b = periods[0], periods[-1]
    plan_shaped = bool(BUDGET_SHEET_RX.search(
        str(model.get("source_sheet") or "")))
    title = (s["dash_t_plan"] if plan_shaped else s["dash_t"])(a, b)

    tiles: List[str] = []
    for role in ("revenue", "opex", "capex", "volume", "volume_secondary"):
        m = met.get(role)
        vals = [v for v in (m or {}).get("values") or [] if v is not None]
        if not vals:
            continue
        tiles.append(
            f'<div class="tile"><div class="tl">'
            f'{_html.escape(roles.get(role, role))}</div>'
            f'<div class="tv">{_fmt(sum(vals), lang)}</div>'
            f'<div class="ts">{_html.escape(s["dash_total"](a, b))} · '
            f'{_html.escape(str(m.get("label", ""))[:34])}</div></div>')
    mpct = (model.get("derived") or {}).get("margin_pct") or []
    last = next((i for i in range(len(mpct) - 1, -1, -1)
                 if mpct[i] is not None), None)
    if last is not None:
        tiles.append(
            f'<div class="tile"><div class="tl">'
            f'{"Marge" if lang == "fr" else "Margin"}</div>'
            f'<div class="tv">{_pct(mpct[last], lang)}</div>'
            f'<div class="ts">{_html.escape(periods[last])}</div></div>')
    tiles = tiles[:5]

    shown = [r for r, _ in (("revenue", GOLD), ("opex", PALE),
                            ("capex", INK3))
             if met.get(r) and any(v is not None for v in met[r]["values"])]
    palette = {"revenue": GOLD, "opex": PALE, "capex": INK3}
    bars = [(met[r]["values"], palette[r]) for r in shown]
    # the legend names the SOURCE series, not just the role — the reader
    # sees exactly which workbook line each colour is
    names = [f'{roles[r]} — {str(met[r].get("label", ""))[:24]}'
             for r in shown]
    svg = _svg_bars(periods, bars, lang, names=names) if bars else ""
    covered = sum(1 for i in range(len(periods))
                  if any(i < len(met[r]["values"])
                         and met[r]["values"][i] is not None for r in shown))
    cap = (f'<p class="cap">{_html.escape(s["dash_cap"])}'
           f'{_html.escape(s["dash_cov"](covered, len(periods)))}</p>'
           if svg else "")
    return _blk(title, f'<div class="tiles">{"".join(tiles)}</div>'
                       f'{svg}{cap}', cls="blk wide")


def _plan_progress_section(model: Dict[str, Any], lang: str) -> str:
    """Part-year actuals against the plan year, role by role — the same
    computed block the exec card shows: % of the plan year vs the declared
    phased expectation, pace stated on the card's thresholds, source
    named, gaps declared. Formatting only; nothing recomputed here."""
    s, roles = _STR[lang], _ROLES[lang]
    pp = (model or {}).get("plan_progress") or {}
    entries = pp.get("roles") or {}
    gaps = pp.get("gaps") or []
    if not entries and not gaps:
        return ""
    rows: List[str] = []
    for role, e in entries.items():
        pct, exp = e.get("pct_of_year"), e.get("expected_pct")
        over = exp is not None and pct is not None and pct > exp * 1.25
        behind = exp is not None and pct is not None and pct < exp * 0.5
        color = CRIT if over else WARN if behind else GOOD
        chip = s["pp_over"] if over else \
            s["pp_behind"] if behind else s["pp_on"]
        fill = max(0.0, min(pct or 0.0, 100.0))
        tick = max(0.0, min(exp or 0.0, 100.0))
        line = s["pp_line"](_fmt(e.get("actual_to_date"), lang),
                            _fmt(e.get("plan_year"), lang),
                            _pct(exp, lang),
                            s["pp_phasing"].get(e.get("phasing"),
                                                str(e.get("phasing"))))
        if e.get("source") == "journal":
            line += f' · {s["pp_journal"]}'
        rows.append(
            f'<div style="margin:0 0 13px">'
            f'<div class="brow" style="padding:2px 0 4px">'
            f'<span class="bl"><b>{_html.escape(roles.get(role, role))}'
            f'</b></span><span></span>'
            f'<span class="bv"><b>{_pct(pct, lang)}</b> · '
            f'<span style="color:{color};font-weight:650">'
            f'{_html.escape(chip)}</span></span></div>'
            f'<div style="height:8px;background:#eee6d2;position:relative">'
            f'<span style="display:block;height:100%;width:{fill:.1f}%;'
            f'background:{CRIT if over else GOLD}"></span>'
            f'<span style="position:absolute;left:{tick:.1f}%;top:-2px;'
            f'bottom:-2px;width:2px;background:{INK3}"></span></div>'
            f'<p class="cap" style="margin-top:5px">{_html.escape(line)}</p>'
            f'</div>')
    gap_html = "".join(
        f'<p class="cap">→ <b>{_html.escape(roles.get(g["role"], g["role"]))}'
        f'</b> — {_html.escape(g["reason"])} · '
        f'{_html.escape(s["pp_needs"](g["requires"]))}</p>'
        for g in gaps)
    return _blk(s["pp_t"](str(pp.get("year"))), "".join(rows) + gap_html)


def _pva_sections(mo: Dict[str, Any], lang: str) -> str:
    s, roles = _STR[lang], _ROLES[lang]
    met = mo.get("metrics") or {}
    periods = mo.get("periods") or []
    out = []
    order = [r for r in ("volume", "volume_secondary", "opex", "capex",
                         "revenue") if r in (mo.get("plan_vs_actual") or {})]
    for role in order:
        v = mo["plan_vs_actual"][role]
        actual = (met.get(role) or {}).get("values") or []
        n_aligned = sum(1 for a, p in zip(actual, v["plan"])
                        if a is not None and p is not None)
        svg = _svg_bars(periods, [(v["plan"], PALE), (actual, GOLD)], lang,
                        names=[s["plan"], s["actual"]])
        cap = s["pva_cap"](_pct(v["pct_of_plan_total"], lang), n_aligned,
                           str(met.get(role, {}).get("label", "")),
                           str(v["plan_label"]))
        out.append(_blk(s["pva_t"](roles.get(role, role)),
                        f'{svg}<p class="cap">{_html.escape(cap)}</p>'))
    return "".join(out)


def _conversion_section(mo: Dict[str, Any], lang: str) -> str:
    s = _STR[lang]
    periods = mo.get("periods") or []
    vr = (mo.get("derived") or {}).get("volume_ratio") or []
    known = [v for v in vr if v is not None]
    if not known:
        return ""
    pcts = [round(v * 100, 2) if v is not None else None for v in vr]
    avg = sum(known) / len(known) * 100
    svg = _svg_bars(periods, [(pcts, GOLD)], lang)
    cap = s["conv_cap"](_pct(round(avg, 1), lang), len(known))
    return _blk(s["conv_t"], f'{svg}<p class="cap">{_html.escape(cap)}</p>')


_JX_KINDS = {
    "en": {"opex": "Operations", "capex": "Investment",
           "overhead": "Head office", "unmapped": "Outside the glossary",
           "dgo": "DGO journal (not consolidated)"},
    "fr": {"opex": "Exploitation", "capex": "Investissement",
           "overhead": "Siège", "unmapped": "Hors glossaire",
           "dgo": "Journal DGO (non consolidé)"},
}


def _cash_destinations_section(jx: Dict[str, Any], lang: str) -> str:
    """Where the cash actually went, from the journal engine: ranked
    destination bars including the DGO journal — the reference
    deliverable's own story."""
    s = _STR[lang]
    dests = jx.get("destinations") or []
    if not dests:
        return ""
    mx = max(d["usd"] for d in dests) or 1
    rows = "".join(
        f'<div class="brow"><span class="bl">'
        f'{_html.escape(_JX_KINDS[lang].get(d["kind"], d["kind"]))}</span>'
        f'<span class="btrack"><span style="width:{max(2, d["usd"] / mx * 100):.0f}%'
        f'{";background:" + CRIT if d["kind"] in ("dgo", "unmapped") else ""}'
        f'"></span></span>'
        f'<span class="bv">{_html.escape(_fmt(d["usd"], lang))} · '
        f'{_html.escape(_pct(d["pct"], lang))}</span></div>'
        for d in dests)
    exc = jx.get("exceptions") or {}
    cap = s["jx_cap"](len(jx.get("months") or []),
                      _fmt(jx.get("total_out_usd"), lang))
    if exc.get("n"):
        cap += " " + s["jx_exc"](exc["n"], _fmt(exc["at_stake_usd"], lang))
    return _blk(s["jx_t"], f'{rows}<p class="cap">{_html.escape(cap)}</p>')


def _cash_section(mo: Dict[str, Any], lang: str) -> str:
    s = _STR[lang]
    cash = mo.get("cash")
    if not cash:
        return ""
    cap_w = cash["capex_total"] / cash["total"] * 100
    bar = (f'<svg viewBox="0 0 640 34" role="img">'
           f'<rect x="0" y="6" width="{cap_w * 6.4:.1f}" height="14" '
           f'fill="{GOLD}"/>'
           f'<rect x="{cap_w * 6.4:.1f}" y="6" '
           f'width="{(100 - cap_w) * 6.4:.1f}" height="14" fill="{PALE}"/>'
           f'<text x="0" y="32" class="ax">{_html.escape(s["capex_lbl"])} '
           f'{_html.escape(_fmt(cash["capex_total"], lang))}</text>'
           f'<text x="640" y="32" text-anchor="end" class="ax">'
           f'{_html.escape(s["opex_lbl"])} '
           f'{_html.escape(_fmt(cash["opex_total"], lang))}</text></svg>')
    cap = s["cash_cap"](_pct(cash["invested_pct"], lang))
    return _blk(s["cash_t"], f'{bar}<p class="cap">{_html.escape(cap)}</p>')


def _unit_cost_section(mo: Dict[str, Any], lang: str) -> str:
    """Monthly $/t bars against the references the data supports: the FY
    budget target (unit_cost_budget) and the phased plan (plan opex ÷ plan
    output, month by month, collapsed to its mean as a line)."""
    s = _STR[lang]
    der = mo.get("derived") or {}
    basis = next((b for b in ("opex_per_volume_out", "opex_per_volume")
                  if any(v is not None for v in der.get(b) or [])), None)
    if basis is None:
        return ""
    vals = der[basis]
    refs = []
    ucb = mo.get("unit_cost_budget")
    if ucb and ucb.get("target"):
        refs.append((s["ref_budget"](ucb["target_period"]), ucb["target"],
                     GOOD))
    pva = mo.get("plan_vs_actual") or {}
    vrole = _vol_role(pva)
    if vrole and "opex" in pva:
        cells = [(o, v) for o, v in zip(pva["opex"]["plan"],
                                        pva[vrole]["plan"])
                 if o is not None and v is not None and v != 0]
        total_v = sum(v for _, v in cells)
        if total_v:
            refs.append((s["ref_phased"],
                         round(sum(o for o, _ in cells) / total_v, 2), WARN))
    # bars colored by state against the tightest reference
    tight = min((r[1] for r in refs), default=None)
    colors = [None if v is None or tight is None
              else CRIT if v > tight * 1.25
              else WARN if v > tight else GOLD for v in vals]
    svg = _svg_bars(mo.get("periods") or [], [(vals, GOLD)], lang,
                    refs=refs, colors=colors)
    last = next((v for v in reversed(vals) if v is not None), None)
    cap = s["uc_cap"](_fmt(last, lang)) if last is not None else ""
    return _blk(s["uc_t"], f'{svg}<p class="cap">{_html.escape(cap)}</p>')


def _outlook_section(mo: Dict[str, Any], lang: str) -> str:
    """The full plan axis vs the run-rate so far: what monthly pace the
    REMAINING months demand to still land the plan — (plan total − actual
    so far) ÷ remaining planned months. Declared, never assumed."""
    s = _STR[lang]
    pva = mo.get("plan_vs_actual") or {}
    plan = mo.get("plan") or {}
    vrole = _vol_role(pva)
    if not (vrole and plan.get("periods")):
        return ""
    label = pva[vrole]["plan_label"]
    pm = next((m for m in (plan.get("metrics") or {}).values()
               if str(m["label"]) == str(label)), None)
    if pm is None:
        return ""
    p_periods = [str(p) for p in plan["periods"]]
    p_vals = pm["values"]
    actual = dict(zip(mo.get("periods") or [],
                      (mo.get("metrics") or {}).get(vrole, {})
                      .get("values") or []))
    a_vals = [actual.get(p) for p in p_periods]
    last_a = max((i for i, v in enumerate(a_vals) if v is not None),
                 default=-1)
    rem = [v for v in p_vals[last_a + 1:] if v is not None]
    plan_total = sum(v for v in p_vals if v is not None)
    act_total = sum(v for v in a_vals if v is not None)
    refs = []
    cap = ""
    if rem and plan_total > act_total:
        required = round((plan_total - act_total) / len(rem), 1)
        known_plan = [v for v in p_vals if v is not None]
        refs.append((s["ref_required"], required, CRIT))
        cap = s["outlook_cap"](_fmt(plan_total, lang), _fmt(required, lang),
                               len(rem),
                               _fmt(round(sum(known_plan)
                                          / len(known_plan), 1), lang))
    svg = _svg_bars(p_periods, [(p_vals, PALE), (a_vals, GOLD)], lang,
                    refs=refs, names=[s["plan"], s["actual"]])
    return _blk(s["outlook_t"],
                f'{svg}<p class="cap">{_html.escape(cap)}</p>')


def _net_cash_section(model: Dict[str, Any], lang: str) -> str:
    """The plan being defended: yearly net balance bars, red below zero,
    with the breakeven year and capital bridge the model already derived."""
    s = _STR[lang]
    net = (model.get("derived") or {}).get("net_cash") or []
    if not any(v is not None for v in net):
        return ""
    periods = model.get("periods") or []
    colors = [None if v is None else (CRIT if v < 0 else GOOD) for v in net]
    svg = _svg_bars(periods, [(net, GOOD)], lang, colors=colors, neg_ok=True)
    ins = model.get("insights") or {}
    parts = []
    if ins.get("cash_positive_period"):
        parts.append(s["breakeven"](ins["cash_positive_period"]))
    if ins.get("capital_bridge") is not None:
        parts.append(s["bridge"](_fmt(ins["capital_bridge"], lang)))
    cap = " · ".join(parts)
    return _blk(s["net_t"], f'{svg}<p class="cap">{_html.escape(cap)}</p>')


def _breakdown_section(model: Dict[str, Any], lang: str) -> str:
    """Ranked destinations from the largest line-item breakdown the
    extraction already computed — where the money actually went."""
    s = _STR[lang]
    bds = model.get("breakdowns") or []
    if not bds:
        return ""
    bd = bds[0]
    items = (bd.get("items") or [])[:6]
    if not items:
        return ""
    mx = max(abs(i["value"]) for i in items) or 1
    rows = "".join(
        f'<div class="brow"><span class="bl">{_html.escape(str(i["label"])[:60])}</span>'
        f'<span class="btrack"><span style="width:{max(2, abs(i["value"]) / mx * 100):.0f}%"></span></span>'
        f'<span class="bv">{_html.escape(_fmt(i["value"], lang))}</span></div>'
        for i in items)
    return _blk(s["bd_t"],
                f'<p class="cap" style="margin:0 0 8px">'
                f'{_html.escape(str(bd.get("sheet", "")))} · '
                f'{_html.escape(str(bd.get("value_col", ""))[:30])}</p>'
                f'{rows}')


def _table_section(mo: Dict[str, Any], lang: str) -> str:
    s, roles = _STR[lang], _ROLES[lang]
    met = mo.get("metrics") or {}
    pva = mo.get("plan_vs_actual") or {}
    periods = mo.get("periods") or []
    order = [r for r in ("volume", "volume_secondary", "opex", "capex",
                         "revenue") if r in met]
    if not order:
        return ""
    # a month column earns its place only when SOMETHING is tracked in it
    # (actual or plan); empty months are dropped and the drop is declared —
    # a 13-column table that is 70% em-dashes buries the three real months
    def _has(i: int) -> bool:
        if any((met[r]["values"][i] if i < len(met[r]["values"]) else None)
               is not None for r in order):
            return True
        return any((v["plan"][i] if i < len(v["plan"]) else None) is not None
                   for v in pva.values())
    keep = [i for i in range(len(periods)) if _has(i)]
    dropped = len(periods) - len(keep)
    head = "".join(f"<th>{_html.escape(_period(periods[i], lang))}</th>"
                   for i in keep)
    rows = []
    for role in order:
        m = met[role]
        cells = "".join(
            f'<td>{_html.escape(_fmt(m["values"][i] if i < len(m["values"]) else None, lang))}</td>'
            for i in keep)
        rows.append(f'<tr><td class="rl">{_html.escape(roles.get(role, role))}'
                    f' — {_html.escape(s["actual"])}</td>{cells}</tr>')
        v = pva.get(role)
        if v:
            pcells = "".join(
                f'<td>{_html.escape(_fmt(v["plan"][i] if i < len(v["plan"]) else None, lang))}</td>'
                for i in keep)
            rows.append(f'<tr class="mut"><td class="rl">'
                        f'{_html.escape(s["plan"])}</td>{pcells}</tr>')
            xcells = "".join(
                f'<td>{_html.escape(_pct(v["pct_of_plan"][i] if i < len(v["pct_of_plan"]) else None, lang))}</td>'
                for i in keep)
            rows.append(f'<tr class="mut"><td class="rl">'
                        f'{_html.escape(s["pct"])}</td>{xcells}</tr>')
    note = (f'<p class="cap">{_html.escape(s["tbl_omitted"](dropped))}</p>'
            if dropped else "")
    return _blk(s["tbl_t"],
                f'<div class="tblwrap"><table><thead><tr>'
                f'<th class="rl">{_html.escape(s["period"])}'
                f'</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody>'
                f'</table></div>{note}', cls="wide")


def render_report_html(report: Dict[str, Any], audit: Optional[Dict[str, Any]],
                       client_name: str, lang: str,
                       blocks: List[str],
                       title: Optional[str] = None) -> str:
    """Typeset the composed report: pure HTML+CSS+SVG, script-free.
    ``title`` overrides the filename-derived default display title."""
    lang = lang if lang in _STR else "en"
    s = _STR[lang]
    esc = _html.escape
    mo = _monthly(report)
    periods = mo.get("periods") or []
    stem = str(report.get("filename") or "").rsplit(".", 1)[0]
    title = (title or "").strip() or s["title"](stem)
    window = s["window"](_period(periods[0], lang),
                         _period(periods[-1], lang), len(periods)) \
        if periods else ""
    today = _dt.date.today().isoformat()

    model = report.get("model") or {}
    parts: List[str] = []
    if "narrative" in blocks:
        parts.append(_narrative_section(report, lang))
    if "dashboard" in blocks:
        parts.append(_dashboard_section(model, lang))
    if "kpi" in blocks:
        tile_html = []
        for k, v, sub, state in _tiles(mo, lang):
            sub_div = f'<div class="ts">{esc(sub)}</div>' if sub else ""
            tile_html.append(
                f'<div class="tile {state}"><div class="tl">{esc(k)}</div>'
                f'<div class="tv">{esc(v)}</div>{sub_div}</div>')
        if tile_html:
            parts.append(f'<div class="tiles">{"".join(tile_html)}</div>')
    cards: List[str] = []
    if "plan_progress" in blocks:
        cards.append(_plan_progress_section(model, lang))
    if "plan_vs_actual" in blocks:
        cards.append(_pva_sections(mo, lang))
    if "unit_cost" in blocks:
        cards.append(_unit_cost_section(mo, lang))
    if "conversion" in blocks:
        cards.append(_conversion_section(mo, lang))
    if "cash" in blocks:
        jx = (report.get("journal") or {}).get("exec")
        cards.append(_cash_destinations_section(jx, lang) if jx
                     else _cash_section(mo, lang))
    if "outlook" in blocks:
        cards.append(_outlook_section(mo, lang))
    if "net_cash" in blocks:
        cards.append(_net_cash_section(model, lang))
    if "breakdowns" in blocks:
        cards.append(_breakdown_section(model, lang))
    grid = "".join(c for c in cards if c)
    if grid:
        parts.append(f'<div class="grid">{grid}</div>')
    if "monthly_table" in blocks:
        parts.append(_table_section(mo, lang))

    ad = audit or {}
    audit_line = s["audit_line"](ad.get("n_verified_relations", 0),
                                 ad.get("n_mismatched_relations", 0),
                                 ad.get("n_unverified_relations", 0))
    return f"""<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
{_font_css()}
  :root {{ --paper:#f4f0e8; --ink:#201d17; --ink2:#5c564a; --ink3:#99917e;
    --gold:#a8821f; --border:#ddd4bf;
    --serif:"Source Serif 4",Georgia,"Times New Roman",serif;
    --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
    font:14px/1.6 var(--serif); }}
  .page {{ max-width:820px; margin:0 auto; padding:52px 34px 60px; }}
  .kicker {{ font:10px/1 var(--mono); letter-spacing:.2em;
    text-transform:uppercase; color:var(--gold); }}
  h1 {{ font-family:var(--serif);
    font-size:28px; font-weight:600; margin:10px 0 6px; line-height:1.25; }}
  .meta {{ font:10.5px/1.6 var(--mono); letter-spacing:.08em;
    text-transform:uppercase; color:var(--ink3); margin-bottom:26px; }}
  .tiles {{ display:flex; flex-wrap:wrap; border-top:1px solid var(--ink);
    border-bottom:1px solid var(--border); margin:0 0 28px; }}
  .tile {{ flex:1 1 140px; padding:12px 14px 14px;
    border-right:1px solid var(--border);
    border-bottom:1px solid var(--border); margin-bottom:-1px; }}
  .tile:last-child {{ border-right:none; }}
  .tl {{ font:9px/1.4 var(--mono); letter-spacing:.12em;
    text-transform:uppercase; color:var(--ink3); }}
  .tv {{ font-family:var(--serif); font-size:22px;
    font-weight:600; margin-top:4px; }}
  .ts {{ font-size:11px; color:var(--ink3); margin-top:3px; line-height:1.4; }}
  .tile {{ border-top:3px solid transparent; }}
  .tile.bad {{ border-top-color:{CRIT}; }} .tile.bad .tv {{ color:{CRIT}; }}
  .tile.warn {{ border-top-color:{WARN}; }} .tile.warn .tv {{ color:{WARN}; }}
  .tile.good {{ border-top-color:{GOOD}; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,
    minmax(330px,1fr)); gap:18px; margin:0 0 26px; }}
  .blk {{ background:#fbf9f3; border:1px solid var(--border);
    padding:14px 16px 16px; }}
  .blk h2 {{ margin-top:0; }}
  .blk svg {{ border:none; background:transparent; }}
  /* sections fold natively — open by default, no scripts */
  details > summary {{ cursor:pointer; list-style:none; }}
  details > summary::-webkit-details-marker {{ display:none; }}
  details > summary h2 {{ display:inline-block; margin:0 0 8px; }}
  details > summary::before {{ content:"▾"; color:var(--gold);
    font:10px var(--mono); margin-right:8px; vertical-align:1px; }}
  details:not([open]) > summary::before {{ content:"▸"; }}
  details.wide {{ margin:0 0 26px; }}
  .nhead {{ font-family:var(--serif); font-size:19px; font-weight:600;
    margin:2px 0 6px; line-height:1.4; }}
  .nwatch {{ margin:10px 0 0; padding-left:18px; font-size:13px;
    color:var(--ink2); }}
  .nwatch li {{ margin-bottom:4px; }}
  .nfoot {{ margin:12px 0 0; font:10px/1.6 var(--mono); letter-spacing:.08em;
    text-transform:uppercase; color:var(--ink3); }}
  /* baked hover readouts: reveal on hover, nothing computed client-side */
  .b .tt {{ opacity:0; pointer-events:none; font:11px var(--mono);
    fill:var(--ink); paint-order:stroke; stroke:#fbf9f3;
    stroke-width:3px; stroke-linejoin:round; }}
  .b:hover .tt {{ opacity:1; }}
  .b:hover rect {{ opacity:.8; }}
  .brow {{ display:grid; grid-template-columns:minmax(110px,1.4fr) 2fr auto;
    gap:10px; align-items:center; padding:5px 0; font-size:12px; }}
  /* row labels never truncate — a clipped label on the biggest cash row
     hides exactly what the reader came for */
  .brow .bl {{ line-height:1.3; overflow-wrap:break-word; }}
  .brow .btrack {{ height:8px; background:#eee6d2; display:block; }}
  .brow .btrack span {{ display:block; height:100%; background:{GOLD}; }}
  .brow .bv {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  h2 {{ font:11px/1.4 var(--mono); letter-spacing:.16em;
    text-transform:uppercase; color:var(--gold); margin:30px 0 10px; }}
  svg {{ display:block; width:100%; height:auto; background:#fbf9f3;
    border:1px solid var(--border); }}
  /* document-first charts: axis text at readable contrast, values printed
     on the page (bv2), gridline values haloed so bars never swallow them */
  .ax {{ font:10px var(--mono); fill:#5c564a; }}
  .gv {{ paint-order:stroke; stroke:#fbf9f3; stroke-width:3px;
    stroke-linejoin:round; }}
  .bv2 {{ font:9.5px var(--mono); fill:#3d3831; paint-order:stroke;
    stroke:#fbf9f3; stroke-width:2.5px; stroke-linejoin:round; }}
  .lg {{ display:flex; gap:16px; flex-wrap:wrap; margin:2px 0 6px;
    font:10.5px var(--mono); letter-spacing:.04em; color:var(--ink2); }}
  .lg .sw {{ display:inline-block; width:9px; height:9px; margin-right:5px;
    vertical-align:-1px; }}
  .tblwrap {{ overflow-x:auto; }}
  .cap {{ margin:8px 0 0; font-size:12.5px; color:var(--ink2);
    max-width:76ch; }}
  table {{ border-collapse:collapse; width:100%; font-size:12.5px;
    font-variant-numeric:tabular-nums; }}
  th, td {{ text-align:right; padding:6px 8px;
    border-bottom:1px solid var(--border); }}
  th {{ font:9.5px var(--mono); letter-spacing:.08em;
    text-transform:uppercase; color:var(--ink3); }}
  .rl {{ text-align:left; }}
  tr.mut td {{ color:var(--ink3); }}
  .foot {{ margin-top:40px; border-top:1px solid var(--border);
    padding-top:12px; font:10px/1.7 var(--mono);
    letter-spacing:.06em; text-transform:uppercase; color:var(--ink3); }}
  @media print {{ body {{ background:#fff; }} .page {{ padding:24px 0; }}
    section, .blk, details {{ break-inside:avoid; }}
    .tiles {{ break-inside:avoid; }}
    /* value labels and gridlines carry the numbers on paper — force the
       ink even when the browser strips backgrounds */
    * {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}
  @media (max-width:720px) {{
    .page {{ padding:28px 16px 40px; }}
    .grid {{ grid-template-columns:1fr; }}
    .tile {{ flex-basis:110px; }} }}
</style></head><body><div class="page">
  <span class="kicker">LUMNIA · {esc(s['kicker'])}</span>
  <h1>{esc(title)}</h1>
  <div class="meta">{esc(client_name)} · {esc(str(report.get('filename') or ''))} · {esc(window)} · {today}</div>
  {''.join(parts)}
  <div class="foot">{esc(audit_line)}<br>{esc(s['footer'])}</div>
</div></body></html>"""
