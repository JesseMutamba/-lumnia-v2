"""Step 1: per-sheet inventory + a general header-row heuristic."""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from .celltypes import cell_kind, BLANK, TEXT


def _row_fill(df: pd.DataFrame, i: int) -> int:
    """Count non-blank cells in row ``i``."""
    return sum(cell_kind(df.iat[i, j]) != BLANK for j in range(df.shape[1]))


def guess_header_row(df: pd.DataFrame, max_scan: int = 25) -> Optional[int]:
    """The v0 general header heuristic.

    Scanning top-down, the header is the first row that is
      * well-filled: >= 50% of the widest row's fill count, and
      * mostly text: >= 50% of its own filled cells are text.

    This skips sparse title banners (well-filled fails) and stops before a
    numeric data block (mostly-text fails). Returns ``None`` when no such row
    exists — we decline rather than guess.
    """
    n_rows, n_cols = df.shape
    if n_rows == 0 or n_cols == 0:
        return None

    limit = min(n_rows, max_scan)
    fills = [_row_fill(df, i) for i in range(limit)]
    widest = max(fills) if fills else 0
    if widest == 0:
        return None

    for i in range(limit):
        filled = fills[i]
        if filled == 0 or filled < 0.5 * widest:
            continue
        text = sum(cell_kind(df.iat[i, j]) == TEXT for j in range(n_cols))
        if text >= 0.5 * filled:
            return i
    return None


def _nonempty_rows(df: pd.DataFrame) -> int:
    return sum(_row_fill(df, i) > 0 for i in range(df.shape[0]))


def _nonempty_cols(df: pd.DataFrame) -> int:
    count = 0
    for j in range(df.shape[1]):
        if any(cell_kind(df.iat[i, j]) != BLANK for i in range(df.shape[0])):
            count += 1
    return count


def profile_sheet(name: str, df: pd.DataFrame, preview_rows: int = 6,
                  preview_cols: int = 12) -> dict:
    """Return a Step-1 inventory dict for one raw sheet."""
    n_rows, n_cols = df.shape
    total = n_rows * n_cols
    non_blank = sum(
        cell_kind(df.iat[i, j]) != BLANK
        for i in range(n_rows)
        for j in range(n_cols)
    ) if total else 0

    return {
        "name": name,
        "n_rows": int(n_rows),
        "n_cols": int(n_cols),
        "n_nonempty_rows": _nonempty_rows(df),
        "n_nonempty_cols": _nonempty_cols(df),
        "fill_ratio": round(non_blank / total, 4) if total else 0.0,
        "header_row": guess_header_row(df),
        "preview": preview_grid(df, preview_rows, preview_cols),
    }


def preview_grid(df: pd.DataFrame, rows: int, cols: int) -> List[List[Optional[object]]]:
    """A JSON-safe top-left slice of the raw sheet."""
    from .jsonsafe import jsonify

    r = min(df.shape[0], rows)
    c = min(df.shape[1], cols)
    return [[jsonify(df.iat[i, j]) for j in range(c)] for i in range(r)]
