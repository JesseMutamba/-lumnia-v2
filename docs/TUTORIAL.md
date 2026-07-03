# Lumnia — user tutorial

Lumnia turns messy spreadsheets into audited, decision-ready dashboards.
This is the operator's guide to everything in the app. (Setup/deploy live in
`DEPLOY.md`; sales assets in `SALES_KIT.md`.)

---

## 1. First contact

Open the site and **drop any CSV or Excel file** anywhere on the page (or
click to browse). Multi-sheet workbooks, French labels, merged headers,
side-by-side tables — all fine; messy is the point.

In a few seconds you land on the **Overview**: a greeting, a colored verdict
chip, and a plain-language summary of what just happened.

The verdict is the first thing to read:

- **✓ Consistent / Cohérent** — every automated check passed; the file's
  internal math holds.
- **? Review recommended** — nothing broken, but some totals couldn't be
  verified automatically.
- **✕ Needs corrections** — the numbers contradict each other somewhere;
  the summary says in how many places and for how much money.

## 2. Two views, two languages

Top right:

- **FR / EN** — the interface language. It's auto-detected from your
  browser; the toggle overrides it. Numbers follow (11,7 M vs 11.7M), and
  the AI narrative writes in the chosen language.
- **Direction / Analyste** (Executive / Analyst) — who the screen is for.
  *Direction* speaks in sentences and hides the machinery: give this one to
  a manager. *Analyste* shows everything: the findings table with formulas,
  sheet extraction detail, filters, CSV exports.

Everything below exists in both views unless noted.

## 3. The Briefing tab — your personal analyst

This is the storytelling engine. It appears whenever the file has at least
one table with numbers.

**Step 1 — the brief.** Lumnia asks four questions:

1. *What is your role?* (chips for common roles, or type your own)
2. *What are your business goals?* (one per line)
3. *What questions do you need answered?* (one per line — this is the
   important one; write real questions: "How is revenue trending?",
   "Which products perform best?", "Are we out of stock anywhere?")
4. *How often will you review this?*

If this client workspace already has a brief from a previous file, a
one-tap **"Use the brief from …"** button reuses it.

**Step 2 — the plan.** Lumnia proposes what it will measure, question by
question:

- **✓ answerable** — with chips for each metric it will compute (untick
  any you don't want);
- **✕ can't be answered from this file** — with exactly what's missing
  ("needs a stock-on-hand or inventory column"). Lumnia never fakes an
  answer to fill a gap.

Click **Build my dashboard**.

**Step 3 — the briefing.** The page reads top to bottom like an analyst
walking you through:

- the headline number with its month-over-month / year-over-year deltas
  beside it;
- **Key takeaways** — one-liners computed from the page's own figures;
- sections titled by *your questions*, in your order, with charts chosen
  automatically (trend → line, few categories → bars, long lists → tables
  with red/green fills). Color means something: only significant moves get
  red/green; the middling majority stays gray;
- **Recommended next steps** — each with its "because …" pointing at a
  figure on the page;
- **What this file can't answer** — the honest list, with what would
  unlock each item.

Use **✎ edit brief** or **↺ change plan** any time. The brief survives
re-analysis; the numbers refresh underneath it.

## 4. Financials & Scénarios (financial workbooks)

When a workbook contains year-projection tables (budgets, business plans),
two extra tabs appear:

- **Finances** — revenue trajectory, margin, CAPEX, unit economics and
  conversion rates, detected from labels (French first). Panels only appear
  when the data actually supports them.
- **Scénarios** — drag Prix / Volumes / Coûts sliders ("−30 %") to stress
  the plan; preset Pessimiste/Référence/Optimiste cards state their
  assumptions in words. Below it, the **Test de résistance** runs thousands
  of simulations and answers "how sure can we be": P10/P50/P90 and the
  probability of hitting targets.

## 5. Tendances / Données (Trends / Data)

Every chartable series the file contains, even outside your brief. Hover
for values, drag to zoom (double-click resets), click legend entries to
hide series. Monthly charts have a **Σ cumul** button that overlays the
running total and the per-month average. In Analyst view this tab also
shows the data profile (completeness, duplicates, outliers) and the raw
sheet cards.

## 6. À faire / Audit (Actions / Audit)

The trust layer — read this before circulating any numbers.

- In **Direction** view: plain-language action cards. "In this sheet,
  'Total' should equal the sum of Jan–Dec — but 4 of 21 rows don't match.
  **Fix now:** change O6 from 0.21 to 1.25…" Cells Lumnia can't determine
  safely say "check with the file's owner" instead of guessing.
- In **Analyste** view: the full findings table — every discovered
  relation, verified or broken, with the worst row and suggested fix.
- **⤓ Corrections CSV** downloads the fixable cells. The loop: fix them in
  the original file, save, re-upload — the status bar tells you how much of
  the discrepancy you closed.

A note on honesty: **unverified** doesn't mean wrong — it means the total's
structure was too unusual to model, so a human should glance at it.

## 7. Sharing and clients

- **↗ Partager / Share** copies a read-only link. Anyone with it sees the
  dashboards — including the built briefing — but no uploads, no editing,
  no library, and it works without the site password. Delete the analysis
  (or revoke) and the link dies.
- **client** button on any workbook in the sidebar assigns it to a client
  workspace; the library groups by client. Monthly re-uploads for the same
  client inherit the brief.
- **rerun** re-analyzes a stored file with the current pipeline — old
  uploads get new features without re-uploading.

## 8. Report & AI narrative

- **⎙ Rapport / Report** prints a branded one-pager (verdict, key figures,
  financial KPIs, two charts, actions) — use the browser's "Save as PDF".
- **✦ Executive narrative** (Overview): one click and Claude writes the
  story of the file — headline, prose, watchouts — in your language,
  addressed to your brief. It only phrases numbers the pipeline verified;
  it never computes. Requires `ANTHROPIC_API_KEY` on the server; without
  it the button simply doesn't appear.

## 9. What makes a file work well

- **Any file** gets the audit + data profile.
- **Categories + amounts** (region, product, montant…) unlock breakdowns,
  best/worst lists and the briefing.
- **A date column** unlocks trends, month-over-month and year-over-year.
  Dates can be `2025-01-31` or `31/01/2025` — both are read correctly.
- **A stock/inventory column** unlocks the stock metrics.
- **Year-projection tables** (2025 | 2026 | …) unlock Finances and
  Scénarios.

The dashboard never pretends: if a section is missing, the file lacks the
ingredient — and the briefing's gap card tells you which one.
