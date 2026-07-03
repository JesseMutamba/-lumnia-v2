# Lumnia sales kit

Three assets: the 60-second demo script, the standing demo link, and the
one-page traction memo you keep updated with real numbers. Everything here
assumes the live site (`fly deploy` from current `main`).

---

## 1. The 60-second demo (script)

Do this live, or record it once with Loom and reuse the link everywhere.
No narration needed beyond the bolded lines.

1. Open the site → **"This is Lumnia. You drop in a real spreadsheet —
   the messy kind."** Drag in the montage workbook.
2. Wait ~4 seconds → the verdict appears. **"Before showing you a single
   chart, it audited the file: the numbers disagree in 9 places, worth
   111.9K. Four cells it can fix itself."**
3. Point at the hero numbers. **"Then it builds the dashboard on the
   numbers that survived: revenue trajectory, margin, even the CPO/FFB
   extraction rate — it discovered that on its own, from the labels."**
4. Click **Scénarios** → drag the price slider to −30%. **"And you can
   stress-test the plan — what happens to revenue if prices drop a third."**
5. Click **↗ Partager**, paste the link into a new tab. **"This read-only
   link is what your client sees. No login, no Excel, in their language."**

Close: **"The first customer paid $3,600 for exactly this on their own
files."**

## 2. The standing demo link

Mint it once on the live site so every email can carry the same URL:

1. Upload the montage workbook (or your best-looking real file with the
   owner's permission — anonymize first if needed).
2. Assign it to a client workspace called `DEMO` (the "client" button in
   the sidebar) so it stays separate from real client files.
3. Click **↗ Share** — the link is copied. This URL is stable until you
   delete the analysis or revoke the share.
4. Put that link in your email signature, the traction memo, and the YC
   application. It works on phones.

Regenerate after a `rerun` if you want the demo to show the newest
pipeline output (the link survives reruns; the content updates).

## 3. The traction memo (fill in and keep current)

Copy the template below into a doc you can share as one page. Update it
weekly — the dated history is the asset.

---

### Lumnia — traction memo *(updated: ____)*

**One-liner:** Lumnia turns messy operator spreadsheets into audited
dashboards — it verifies the math before it charts anything.

**Live demo:** ____ *(the standing share link)* · **Video:** ____ *(Loom)*

**Revenue to date:** $3,600 *(first customer: ____ — what they bought: ____)*

**Usage (update weekly):**

| Week | Workbooks analyzed | Active users | Returning users | Notes |
|------|-------------------:|-------------:|----------------:|-------|
| ____ | | | | |
| ____ | | | | |
| ____ | | | | |

**What users say** *(verbatim, with permission)*:
> ____ — *(name, role, company)*
> ____
> ____

**Why now:** general AI tools guess; operators need numbers they can
defend. Lumnia audits first — on the first real client file it found
111.9K of internal contradictions before charting. French-first, no data
team required, works from a single file drop.

**The wedge:** francophone agricultural operators and the accountants and
co-ops who inherit their spreadsheets; each accountant touches dozens of
businesses.

**Ask:** ____ *(what you want from the reader: intro, pilot, investment)*

---

### Where the weekly numbers come from

The app reports its own usage — no SQL, no SSH. Hit the `/stats` endpoint
(behind the shared password) on the deployed site:

```bash
curl -s https://YOUR-APP/stats | python3 -m json.tool
```

It returns exactly what the table above needs:

- `total_analyses`, `total_sheets`, `n_clients`, `days_active`
- `first_upload` / `last_upload`
- `by_week` — `[{ "week": "2026-06-29", "count": 2 }, ...]` → one row of the
  table per entry
- `by_client` — per-client counts (your workspaces)

If the site is password-gated, log in in the browser first and open
`/stats` there, or pass the session cookie to curl.
