"""Demo rehearsal gate: reset, ingest N times, verify the arc's numbers.

Usage:
    python scripts/demo_check.py                      # synthetic rehearsal book
    python scripts/demo_check.py --file pvak.xlsx     # the real demo file
    python scripts/demo_check.py --reset-only         # back to a clean slate

Each round wipes the scratch demo store (LUMNIA_DB/LUMNIA_FILES point at
./demo-state, never the production store), uploads the workbook through the
real API in-process, and asserts what the demo needs on screen: the model
renders, the monthly cost-vs-budget block exists, every cost-side metric
names its source cells, and the trace endpoint reproduces each cited value
from the stored bytes. Green 5/5 or the demo is not ready.

The synthetic book is rehearsal material only — French, cross-tab, three
sheets, banner-labeled DONNÉES SYNTHÉTIQUES — and is never client data.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "demo-state"

MONTHS = ["janv. 2025", "févr. 2025", "mars 2025",
          "avr. 2025", "mai 2025", "juin 2025"]


def synthetic_book() -> bytes:
    import pandas as pd
    actuals = pd.DataFrame([
        ["SUIVI MENSUEL PRODUCTION — DONNÉES SYNTHÉTIQUES (RÉPÉTITION)",
         *([None] * 6)],
        ["RUBRIQUE", *MONTHS],
        ["PRODUCTION FFB (T)", 190, 210, 200, 225, 205, 200],
        ["PRODUCTION CPO (T)", 38, 42, 40, 45, 41, 40],
        ["CHARGES D'EXPLOITATION (USD)",
         34200, 37800, 36000, 40500, 36900, 39080],   # juin: 977 $/t
        ["TOTAL PRODUCTION (T)", 228, 252, 240, 270, 246, 240],
    ])
    budget = pd.DataFrame([
        ["BUDGET PRÉVISIONNEL — DONNÉES SYNTHÉTIQUES", None, None, None],
        ["POSTE", 2024, 2025, 2026],
        ["PRODUCTION FFB PREVUE (T)", 5600, 6000, 6600],
        ["PRODUCTION CPO PREVUE (T)", 1150, 1200, 1300],
        ["CHARGES D'EXPLOITATION PREVUES (USD)",
         529000, 552000, 598000],                     # 460 $/t for 2025
    ])
    plan = pd.DataFrame([
        ["PLAN OPÉRATIONNEL MENSUEL — DONNÉES SYNTHÉTIQUES", *([None] * 6)],
        ["RUBRIQUE", *MONTHS],
        ["PRODUCTION CPO PREVUE (T)", 45, 46, 46, 47, 47, 47],
        ["CHARGES PREVUES (USD)", 30000, 30500, 31000, 31000, 31500, 31500],
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        actuals.to_excel(xw, sheet_name="SUIVI PRODUCTION 2025",
                         header=False, index=False)
        budget.to_excel(xw, sheet_name="BUDGET 2025", header=False, index=False)
        plan.to_excel(xw, sheet_name="PLAN MENSUEL 2025",
                      header=False, index=False)
    return buf.getvalue()


def reset_state() -> None:
    shutil.rmtree(STATE, ignore_errors=True)
    STATE.mkdir()


def check_round(client, name: str, content: bytes, rnd: int) -> None:
    r = client.post("/analyze", files={
        "file": (name, content, "application/octet-stream")})
    assert r.status_code == 200, f"ingest failed: {r.text[:300]}"
    rep = r.json()
    model = rep.get("model") or {}
    mo = model.get("monthly")
    assert mo, "no monthly block — the dashboard would be empty"
    ub = mo.get("unit_cost_budget")
    assert ub, "no cost-vs-budget block — the anomaly would not render"
    last = [v for v in ub["variance_pct"] if v is not None][-1]
    assert ub.get("target_sources"), "budget target names no source cells"

    # every figure the demo click can land on must re-derive from its cited
    # cells via the trace endpoint: metric values, the budget target's
    # sources, and the plan side of every attainment chip
    n_checked = 0

    def _trace(sheet: str, refs: list, expect=None) -> None:
        nonlocal n_checked
        t = client.get(f"/analyses/{rep['id']}/cells",
                       params={"sheet": sheet, "refs": ",".join(refs)})
        assert t.status_code == 200, (sheet, refs, t.text[:300])
        if expect is not None:
            total = sum(c["value"] for c in t.json()["cells"])
            assert round(total, 4) == expect, (sheet, refs, total, expect)
        n_checked += 1

    for role, m in mo["metrics"].items():
        cells = m.get("cells")
        assert cells, f"metric '{role}' carries no source cells"
        assert len(cells) == len(m["values"]), role
        for v, refs in zip(m["values"], cells):
            if v is not None and refs:
                _trace(m["sheet"], refs, expect=v)
    for role, src in ub["target_sources"].items():
        _trace(src["sheet"], src["cells"])
    for role, p in (mo.get("plan_vs_actual") or {}).items():
        cells = p.get("plan_cells")
        if not cells:
            continue
        assert len(cells) == len(p["plan"]), role
        for v, refs in zip(p["plan"], cells):
            if v is not None and refs:
                _trace(p["plan_sheet"], refs, expect=v)
    assert n_checked > 0, "nothing traced — every demo click would be dead"
    print(f"  round {rnd}: ingest ok · variance {last:+}% vs budget "
          f"{ub['target_period']} · {n_checked} figures trace to source")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="demo workbook (default: synthetic)")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--reset-only", action="store_true",
                    help="wipe demo state and exit")
    args = ap.parse_args()

    reset_state()
    if args.reset_only:
        print("demo state reset — clean slate")
        return 0

    # the app must open its stores inside demo-state, never production
    os.environ["LUMNIA_DB"] = str(STATE / "demo.db")
    os.environ["LUMNIA_FILES"] = str(STATE / "files")
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from app.main import app

    if args.file:
        content = Path(args.file).read_bytes()
        name = Path(args.file).name
    else:
        content = synthetic_book()
        name = "demo_rehearsal_synthetique.xlsx"

    with TestClient(app) as client:
        for rnd in range(1, args.rounds + 1):
            reset_state()
            check_round(client, name, content, rnd)
    print(f"{args.rounds}/{args.rounds} green — the arc's numbers hold. "
          "Run --reset-only before going on stage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
