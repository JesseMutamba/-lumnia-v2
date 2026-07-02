"""Step 1: per-sheet inventory + a general header-row heuristic."""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from .celltypes import grid_kinds, BLANK, TEXT


def guess_header_row(df: pd.DataFrame, max_scan: int = 25,
                     kinds: Optional[List[List[str]]] = None) -> Optional[int]:
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
    if kinds is None:
        kinds = grid_kinds(df)

    limit = min(n_rows, max_scan)
    fills = [sum(k != BLANK for k in kinds[i]) for i in range(limit)]
    widest = max(fills) if fills else 0
    if widest == 0:
        return None

    for i in range(limit):
        filled = fills[i]
        if filled == 0 or filled < 0.5 * widest:
            continue
        text = sum(k == TEXT for k in kinds[i])
        if text >= 0.5 * filled:
            return i
    return None


def profile_sheet(name: str, df: pd.DataFrame, preview_rows: int = 6,
                  preview_cols: int = 12,
                  kinds: Optional[List[List[str]]] = None) -> dict:
    """Return a Step-1 inventory dict for one raw sheet."""
    n_rows, n_cols = df.shape
    if kinds is None:
        kinds = grid_kinds(df)
    total = n_rows * n_cols

    row_fills = [sum(k != BLANK for k in kinds[i]) for i in range(n_rows)]
    non_blank = sum(row_fills)
    nonempty_cols = sum(
        any(kinds[i][j] != BLANK for i in range(n_rows))
        for j in range(n_cols)
    ) if n_rows else 0

    return {
        "name": name,
        "n_rows": int(n_rows),
        "n_cols": int(n_cols),
        "n_nonempty_rows": sum(f > 0 for f in row_fills),
        "n_nonempty_cols": nonempty_cols,
        "fill_ratio": round(non_blank / total, 4) if total else 0.0,
        "header_row": guess_header_row(df, kinds=kinds),
        "preview": preview_grid(df, preview_rows, preview_cols),
    }


def preview_grid(df: pd.DataFrame, rows: int, cols: int) -> List[List[Optional[object]]]:
    """A JSON-safe top-left slice of the raw sheet."""
    from .jsonsafe import jsonify

    r = min(df.shape[0], rows)
    c = min(df.shape[1], cols)
    return [[jsonify(df.iat[i, j]) for j in range(c)] for i in range(r)]
