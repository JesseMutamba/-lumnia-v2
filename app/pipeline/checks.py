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

import re
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


# A totals row must match on this share of its filled numeric cells...
TOTAL_ROW_COVERAGE = 0.6
# ...with at least this many non-zero matching cells, over a base of >= 2 rows.
TOTAL_ROW_MIN_MATCH = 2

# Generic summary-row labels (not file-specific): a row labelled like this is
# a totals *claim*, detected even when its numbers DON'T add up — those are
# precisely the rows the audit must verify rather than trust.
_TOTAL_LABEL_RE = re.compile(r"\b(total|totaux|totales?|somme|cumul|sum)\b",
                             re.IGNORECASE)


def detect_total_rows(names: List[str],
                      cols: Dict[str, List[Optional[float]]],
                      labels: Optional[List[Optional[str]]] = None) -> List[dict]:
    """Find summary rows by two independent signals:

    * **values** — the row's numeric cells equal the column sums of the data
      rows above it (section sums catch subtotals, the grand sum catches a
      bottom line spanning subtotalled sections). Guards: at least
      TOTAL_ROW_MIN_MATCH non-zero matching cells, TOTAL_ROW_COVERAGE of the
      row's filled cells matching, and a base of >= 2 accumulated rows (so
      constant-value columns can't make every row look like a total of the
      one above it).
    * **label** — the row label reads like a total ("TOTAL", "SOUS-TOTAL",
      "somme", ...). These are totals *claims*: they are detected even when
      broken and verified against whichever base (section or grand) fits
      best, so a total that doesn't add up becomes a finding instead of
      silently passing as data.

    Returns ``[{"i": record_index, "base": {col: expected_sum}}]``.
    """
    if not cols:
        return []
    n = len(next(iter(cols.values())))
    section = {name: 0.0 for name in cols}
    grand = {name: 0.0 for name in cols}
    section_rows = grand_rows = 0
    out = []
    for i in range(n):
        row = {name: cols[name][i] for name in cols if cols[name][i] is not None}
        if not row:
            continue

        is_total, base_used = False, None
        candidates = [(section, section_rows), (grand, grand_rows)]
        for base, base_rows in candidates:
            if base_rows < 2:
                continue
            matched = [name for name, v in row.items() if _close(base[name], v)]
            nonzero = sum(1 for name in matched if row[name] != 0)
            if (nonzero >= TOTAL_ROW_MIN_MATCH
                    and len(matched) >= TOTAL_ROW_COVERAGE * len(row)):
                is_total, base_used = True, dict(base)
                break

        label = (labels[i] if labels else None) or ""
        if not is_total and _TOTAL_LABEL_RE.search(label):
            # A claimed total: verify against whichever base explains it best.
            scored = [
                (sum(_close(base[name], v) for name, v in row.items()), dict(base))
                for base, base_rows in candidates if base_rows >= 1
            ]
            if scored:
                is_total, base_used = True, max(scored, key=lambda s: s[0])[1]

        if is_total:
            out.append({"i": i, "base": base_used})
            section = {name: 0.0 for name in cols}
            section_rows = 0
        else:
            for name, v in row.items():
                section[name] += v
                grand[name] += v
            section_rows += 1
            grand_rows += 1
    return out


def check_column_sums(names: List[str], cols: Dict[str, List[Optional[float]]],
                      rows: List[int], labels: List[Optional[str]],
                      totals: List[dict]) -> List[dict]:
    """One finding per detected totals row: does every declared total match
    the column sum it summarises? Mismatch entries reuse the standard shape
    with ``label`` naming the offending *column*.

    Fail honest: when the row disagrees with the base on most columns, or
    agrees only on zeros, it is OUR model of what it sums that is wrong
    (hierarchical sections with parent+child rows both present, summaries of
    a different block, ...) — the finding is reported ``unverified`` with no
    fabricated expected/actual claims, rather than as a false mismatch.
    """
    findings = []
    for t in totals:
        i, base = t["i"], t["base"]
        declared = {name: cols[name][i] for name in cols
                    if cols[name][i] is not None}
        matched = {name: v for name, v in declared.items()
                   if _close(base[name], v)}
        nonzero_matched = sum(1 for v in matched.values() if v != 0)
        who = labels[i] or f"row {rows[i]}"

        if len(matched) < len(declared) and (
                len(matched) < 0.5 * len(declared) or nonzero_matched < 1):
            findings.append({
                "kind": "column_sum",
                "formula": (f"totals row {rows[i]} ({who}) could not be "
                            f"verified against the rows above it"),
                "target": who, "inputs": [],
                "n_checked": len(declared), "n_matched": len(matched),
                "n_mismatched": 0, "total_abs_delta": 0.0,
                "mismatches": [], "status": "unverified",
            })
            continue

        mism = [_mismatch(rows[i], name, base[name], v)
                for name, v in declared.items() if name not in matched]
        findings.append(_finding(
            "column_sum",
            f"totals row {rows[i]} ({who}) = column sums of the rows above",
            who, [], len(declared), len(declared) - len(mism), mism))
    return findings


def check_products(names: List[str], cols: Dict[str, List[Optional[float]]],
                   rows: List[int], labels: List[Optional[str]],
                   exclude: Optional[set] = None) -> List[dict]:
    numeric = [n for n in names if n in cols]
    if len(numeric) > MAX_PRODUCT_COLS:
        return []
    exclude = exclude or set()
    findings = []
    for target in numeric:
        c = cols[target]
        best = None
        for a_name, b_name in combinations((n for n in numeric if n != target), 2):
            a, b = cols[a_name], cols[b_name]
            if _is_degenerate(a) or _is_degenerate(b):
                continue
            idx = [i for i in range(len(c)) if i not in exclude
                   and a[i] is not None and b[i] is not None and c[i] is not None]
            if len(idx) < MIN_SUPPORT_ROWS:
                continue
            hits = [i for i in idx if _close(a[i] * b[i], c[i])]
            if len(hits) < max(PRODUCT_MIN_MATCHED, MIN_SUPPORT_FRAC * len(idx)):
                continue
            # 0 x anything = 0 rows prove nothing; a real relation needs
            # enough matches with actual values in them (zero-heavy sheets
            # otherwise "support" any product involving their zeros).
            informative = sum(1 for i in hits if c[i] != 0)
            if informative < PRODUCT_MIN_MATCHED:
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
                   rows: List[int], labels: List[Optional[str]],
                   exclude: Optional[set] = None) -> List[dict]:
    numeric = set(cols)
    exclude = exclude or set()
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
                if c[i] is None or i in exclude:
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
            if sum(1 for i in hits if c[i] != 0) < MIN_SUPPORT_ROWS:
                continue                     # all-zero agreement proves nothing
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
               row_labels: List[Optional[str]],
               totals: Optional[List[dict]] = None) -> Optional[List[dict]]:
    """Discover + verify relations. Detected totals rows are verified against
    their column sums and excluded from the row-level relation checks (they
    are derived rows, not observations). Returns findings sorted with
    mismatches (largest money impact) first, or ``None`` when nothing was
    checkable."""
    if totals is None:
        totals = detect_total_rows(names, numeric_cols, row_labels)
    exclude = {t["i"] for t in totals}
    findings = (
        check_column_sums(names, numeric_cols, row_numbers, row_labels, totals)
        + check_products(names, numeric_cols, row_numbers, row_labels, exclude)
        + check_row_sums(names, numeric_cols, row_numbers, row_labels, exclude)
    )
    if not findings:
        return None
    findings.sort(key=lambda f: (f["status"] == "ok", -f["total_abs_delta"]))
    return findings
