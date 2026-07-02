"""Read an uploaded file into raw, uninterpreted DataFrames.

Contract: read *every* sheet with ``header=None`` and no cleaning. We make no
assumptions about where the header is or what the data means — that is the job
of later stages. The only decision here is CSV vs Excel.
"""
from __future__ import annotations

import io
from typing import Dict

import pandas as pd

_EXCEL_EXT = (".xlsx", ".xlsm", ".xls", ".xlsb")
_CSV_EXT = (".csv", ".tsv", ".txt")


def read_upload(content: bytes, filename: str) -> Dict[str, pd.DataFrame]:
    """Return ``{sheet_name: raw DataFrame}`` for the uploaded bytes.

    Excel files yield one entry per sheet; CSV/TSV yield a single entry keyed
    by the sheet-less filename stem. Every frame is read ``header=None`` and
    ``dtype=object`` so nothing is coerced or dropped.
    """
    name = (filename or "").lower().strip()

    if name.endswith(_EXCEL_EXT):
        return _read_excel(content)
    if name.endswith(_CSV_EXT):
        return _read_csv(content, name)

    # Unknown extension: try Excel first (it self-validates via magic bytes),
    # then fall back to CSV. Fail honest if neither works.
    try:
        return _read_excel(content)
    except Exception:
        return _read_csv(content, name or "data")


def _read_excel(content: bytes) -> Dict[str, pd.DataFrame]:
    sheets = pd.read_excel(
        io.BytesIO(content),
        sheet_name=None,      # all sheets
        header=None,          # no header interpretation
        dtype=object,         # keep values as-is
        engine="openpyxl",
    )
    return {str(k): v for k, v in sheets.items()}


def _read_csv(content: bytes, name: str) -> Dict[str, pd.DataFrame]:
    sep = "\t" if name.endswith(".tsv") else None  # None -> sniff
    df = pd.read_csv(
        io.BytesIO(content),
        header=None,
        dtype=object,
        sep=sep,
        engine="python",      # needed for sep sniffing / ragged rows
        skip_blank_lines=False,
    )
    stem = name.rsplit("/", 1)[-1]
    for ext in _CSV_EXT:
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return {stem or "data": df}
