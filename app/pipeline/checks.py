"""Step 4: deterministic reconciliation checks over extracted tidy tables.

Inspired by audit tooling in trade/logistics (recompute what a line *should*
say from its own inputs, diff against what it *does* say, and quantify the
discrepancy): instead of only profiling a spreadsheet, we let it check itself.

Two relation families are *discovered* — never assumed from column names:

* product : ``C ≈ A × B``          (e.g. Montant = Qté × Cout unit)
* row sum : ``C ≈ sum(run)``       (e.g. Montant = Janvier + ... + Décembre)

A relation is only reported when it actually holds on a solid majority of the
checkable rows (``MIN_SUPPORT_FRAC``, at least ``MIN_SUPPORT_ROWS`` of them) —
then every violating row becomes a finding with expected/actual/delta and the
original sheet row number, so a human can open Excel and look at that line.

Fail honest: too few checkable rows, or too many numeric columns to scan
exhaustively, and we return nothing rather than noise.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Optional

# A candidate relation must be checkable on this many rows...
MIN_SUPPORT_ROWS = 3
# ...and hold on this fraction of them to be considered real.
MIN_SUPPORT_FRAC = 0.7
# Products are more coincidence-prone than sums (any two columns whose values
# multiply near a third on a few rows): require more matching rows.
PRODUCT_MIN_MATCHED = 5
# Match tolerance: spreadsheets round; 1% relative or 0.01 absolute.
TOL_REL = 0.01
TOL_ABS = 0.01
# Skip product discovery beyond this many numeric columns (O(n^3) pairs).
MAX_PRODUCT_COLS = 30
# Report at most this many violating rows per relation.
MAX_MISMATCHES = 10


def _close(expected: float, actual: float) -> bool:
    return abs(expected - actual) <= max(TOL_ABS, TOL_REL * abs(actual))


def _mismatch(row_number: int, label: Optional[str], expected: float,
              actual: float) -> Dict[str, Any]:
    return {
        "row": row_number,                    # 1-based sheet row, as Excel shows it
        "label": label,
        "expected": round(expected, 4),
        "actual": round(actual, 4),
        "delta": round(actual - expected, 4),
    }


def _finding(kind: str, formula: str, target: str, inputs: List[str],
             checked: int, matched: int,
             mismatches: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "kind": kind,
        "formula": formula,
        "target": target,
        "inputs": inputs,
        "n_checked": checked,
        "n_matched": matched,
        "n_mismatched": checked - matched,
        "total_abs_delta": round(sum(abs(m["delta"]) for m in mismatches), 4),
        "mismatches": mismatches[:MAX_MISMATCHES],
        "status": "ok" if checked == matched else "mismatch",
    }


def _is_degenerate(vals: List[Optional[float]]) -> bool:
    """A factor column of all 0s/1s makes C = A x B a trivial identity."""
    present = {v for v in vals if v is not None}
    return present <= {0.0, 1.0}


def check_products(names: List[str], cols: Dict[str, List[Optional[float]]],
                   rows: List[int], labels: List[Optional[str]]) -> List[dict]:
    numeric = [n for n in names if n in cols]
    if len(numeric) > MAX_PRODUCT_COLS:
        return []
    findings = []
    for target in numeric:
        c = cols[target]
        best = None
        for a_name, b_name in combinations((n for n in numeric if n != target), 2):
            a, b = cols[a_name], cols[b_name]
            if _is_degenerate(a) or _is_degenerate(b):
                continue
            idx = [i for i in range(len(c))
                   if a[i] is not None and b[i] is not None and c[i] is not None]
            if len(idx) < MIN_SUPPORT_ROWS:
                continue
            hits = [i for i in idx if _close(a[i] * b[i], c[i])]
            if len(hits) < max(PRODUCT_MIN_MATCHED, MIN_SUPPORT_FRAC * len(idx)):
                continue
            if best is None or len(hits) > best[0]:
                best = (len(hits), idx, a_name, b_name)
        if best is None:
            continue
        n_hits, idx, a_name, b_name = best
        a, b = cols[a_name], cols[b_name]
        mism = [_mismatch(rows[i], labels[i], a[i] * b[i], c[i])
                for i in idx if not _close(a[i] * b[i], c[i])]
        findings.append(_finding(
            "product", f"{target} = {a_name} x {b_name}",
            target, [a_name, b_name], len(idx), len(idx) - len(mism), mism))

    # C = A x B, A = C / B etc. are the same relation seen from different
    # targets; keep only the best-supported finding per column triple.
    by_triple: Dict[frozenset, dict] = {}
    for f in findings:
        key = frozenset([f["target"], *f["inputs"]])
        if key not in by_triple or f["n_matched"] > by_triple[key]["n_matched"]:
            by_triple[key] = f
    return list(by_triple.values())


def _numeric_runs(names: List[str], numeric: set, min_len: int = 3) -> List[List[str]]:
    """Contiguous runs of numeric columns, in sheet order (e.g. Jan..Dec)."""
    runs, cur = [], []
    for n in names:
        if n in numeric:
            cur.append(n)
        else:
            if len(cur) >= min_len:
                runs.append(cur)
            cur = []
    if len(cur) >= min_len:
        runs.append(cur)
    return runs


def check_row_sums(names: List[str], cols: Dict[str, List[Optional[float]]],
                   rows: List[int], labels: List[Optional[str]]) -> List[dict]:
    numeric = set(cols)
    findings = []
    for run in _numeric_runs(names, numeric):
        run_set = set(run)
        # Candidate (target, parts) pairs: any numeric column outside the run,
        # plus the run's own endpoints — a total column usually sits directly
        # before or after the columns it sums, so it lands inside the run.
        candidates = [(t, run) for t in names
                      if t in numeric and t not in run_set]
        if len(run) >= 4:
            candidates.append((run[0], run[1:]))
            candidates.append((run[-1], run[:-1]))
        for target, parts in candidates:
            c = cols[target]
            idx = []
            for i in range(len(c)):
                if c[i] is None:
                    continue
                vals = [cols[n][i] for n in parts]
                if all(p is None for p in vals):
                    continue
                idx.append(i)
            if len(idx) < MIN_SUPPORT_ROWS:
                continue
            def rowsum(i):   # blank cells inside the run mean 0 (empty month)
                return sum(p for n in parts if (p := cols[n][i]) is not None)
            hits = [i for i in idx if _close(rowsum(i), c[i])]
            if len(hits) < max(MIN_SUPPORT_ROWS, MIN_SUPPORT_FRAC * len(idx)):
                continue
            mism = [_mismatch(rows[i], labels[i], rowsum(i), c[i])
                    for i in idx if not _close(rowsum(i), c[i])]
            findings.append(_finding(
                "row_sum",
                f"{target} = {parts[0]} + ... + {parts[-1]} ({len(parts)} cols)",
                target, list(parts), len(idx), len(idx) - len(mism), mism))
    return findings


def run_checks(names: List[str],
               numeric_cols: Dict[str, List[Optional[float]]],
               row_numbers: List[int],
               row_labels: List[Optional[str]]) -> Optional[List[dict]]:
    """Discover + verify relations. Returns findings sorted with mismatches
    (largest money impact) first, or ``None`` when nothing was checkable."""
    findings = (check_products(names, numeric_cols, row_numbers, row_labels)
                + check_row_sums(names, numeric_cols, row_numbers, row_labels))
    if not findings:
        return None
    findings.sort(key=lambda f: (f["status"] == "ok", -f["total_abs_delta"]))
    return findings
