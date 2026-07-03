# Lumnia vs. the Tableau worldview — positioning brief

*Prepared July 2026. Sources at the bottom. For investor/customer conversations —
especially the inevitable question: "isn't this just Tableau?"*

## 1. What Tableau says (the deep dive)

### Their data-cleaning doctrine (tableau.com/learn/articles/what-is-data-cleaning)

Tableau's canonical article defines data cleaning as fixing or removing
incorrect, corrupted, duplicated or incomplete data, and prescribes a
five-step manual process:

1. Remove duplicate or irrelevant observations
2. Fix structural errors (naming inconsistencies, typos, "N/A" vs "Not Applicable")
3. Filter unwanted outliers
4. Handle missing data
5. Validate and QA

It also names five characteristics of quality data: **validity, accuracy,
completeness, consistency, uniformity** — and concedes there is "no one
absolute way" to do it; every dataset needs its own process, executed by a
person (their answer is Tableau Prep, a tool an analyst drives).

**The point for us:** that article is a job description for a human analyst.
Lumnia executes that exact checklist automatically — duplicates, structural
errors, outliers and missingness are detected by the EDA layer; validation
isn't a "QA step" but the core of the product (every discovered relation is
recomputed and checked). Their five quality characteristics map one-to-one
onto things Lumnia measures and reports per sheet.

### Their agentic-analytics push (tableau.com/agentic-analytics)

Salesforce relaunched Tableau as an "agentic analytics platform" (Tableau
Next, launched 2025, expanded through 2026): AI agents that "augment and
accelerate every stage of the journey from data to insights to action."
The architecture:

- **Tableau Semantics** — a semantic layer inside Salesforce Data 360 that
  gives agents "trusted, unified business data." Agents are only as good as
  this curated layer; someone still has to build and maintain it.
- **Three named agent skills** on Agentforce:
  **Data Pro** (prep/model/visualize), **Concierge** (natural-language Q&A,
  root causes, next-best actions), **Inspector** (proactive monitoring,
  trends, anomalies).
- MCP servers, Slack/Teams/Google Workspace integrations, an "Agentic
  Analytics Command Center."
- Pricing: Tableau Next from ~$40/user/month (Creator, annual); the AI
  bundle (Tableau+) is unlisted, sales-negotiated, and sits on top of the
  Salesforce ecosystem; cloud-only. Industry reporting puts average
  enterprise agentic-AI implementations near $890k, with data quality cited
  by ~52% of businesses as the biggest AI-adoption barrier.

## 2. What this means for Lumnia

**The good news: the market's biggest BI vendor just validated the thesis.**
"Agents that take you from data to decision" is now the stated direction of
the category. Nobody has to be convinced the workflow is valuable.

**The gap they cannot easily close is exactly our wedge:**

1. **They start after the mess; we start with the mess.** Tableau Semantics
   assumes data that has been ingested, modeled and semantically labeled —
   by someone. Our customer's reality is a 9-sheet French workbook with
   merged headers, side-by-side tables and totals that don't add up. Their
   own article says cleaning that is a manual, per-dataset human process.
   That manual process *is* our product, automated.
2. **Their trust flows down; ours flows up.** Their agents answer on top of
   a curated semantic layer — trust is assumed from the layer. Lumnia
   *creates* the trust: every number is deterministically computed, every
   relation cross-checked, discrepancies are quantified in money, and the
   AI is only allowed to phrase verified figures ("it phrases, never
   computes"). When 52% of businesses say data quality blocks their AI
   adoption, an audit-first engine is the missing bottom layer, not a
   competing top layer.
3. **Their buyer has a data team; ours doesn't.** $40+/user/month, Salesforce
   ecosystem, cloud-only, six-figure implementations, English-first. Our
   buyer is a francophone operator or accountant who will never build a
   semantic model — they drop a file and get a verdict in their language.
4. **Their agents are named; their outputs are probabilistic.** Concierge
   generates answers; if the underlying data is wrong, the answer is
   confidently wrong. Lumnia's differentiation in one line: **we audit
   before we chart.**

## 3. The soundbites

- *"Isn't this just Tableau?"* — "Tableau starts where your data is already
  clean and modeled. Lumnia starts where your data actually is: a messy
  workbook nobody fully trusts. We're the layer Tableau assumes exists."
- *"Won't Tableau's agents do this?"* — "Their agents answer questions on
  top of a curated semantic layer someone has to build. Our engine builds
  the trust itself: it found 111.9K of internal contradictions in a real
  client file before drawing a single chart. Their own data-cleaning guide
  says that step is manual."
- *"Why won't Salesforce crush you?"* — "Our first customer paid $3,600 and
  has no data team, no Salesforce contract, and works in French. That
  customer doesn't exist in Tableau's funnel."
- One-liner: **"Lumnia turns messy operator spreadsheets into audited
  dashboards — it verifies the math before it charts anything."**

## 4. Worth borrowing from them

- **Named agent roles.** "Data Pro / Concierge / Inspector" is good
  packaging. Lumnia's stages could be presented the same way (the Reader,
  the Auditor, the Analyst, the Narrator) without changing any code.
- **Inspector's proactive monitoring** is our natural roadmap item: the
  monthly re-upload of the same workbook → automatic delta report ("your
  margin moved 4 points; two new discrepancies appeared").
- **Semantic layer as vocabulary.** Our Step-7 role-tagged model *is* a
  lightweight semantic layer, discovered rather than authored. Use that
  phrase with technical investors.

## 5. Sources

- https://www.tableau.com/learn/articles/what-is-data-cleaning
- https://www.tableau.com/agentic-analytics
- https://www.salesforce.com/news/stories/tableau-agentic-analytics-platform-announcement/
- https://www.tableau.com/blog/agentic-analytics-new-paradigm-for-business-intelligence
- https://www.tableau.com/products/tableau-next
- https://www.techtarget.com/searchbusinessanalytics/news/366622614/Tableau-enters-the-agentic-AI-era-with-the-launch-of-Next
- https://www.salesforceben.com/salesforce-introduces-agentic-analytics-in-tableau/
- https://vendorbenchmark.com/vendors/tableau-salesforce-pricing
- https://axis-intelligence.com/agentic-ai-adoption-statistics-2026/

*Note: the two tableau.com pages themselves block automated access; their
content above is reconstructed from search coverage, Salesforce's press
material, and third-party analyses. Spot-check quotes in a browser before
using them verbatim on stage.*
