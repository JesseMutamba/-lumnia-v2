"""Read an uploaded file into raw, uninterpreted DataFrames.

Contract: read *every* sheet with ``header=None`` and no cleaning. We make no
assumptions about where the header is or what the data means — that is the job
of later stages. The only decision here is CSV vs Excel.
"""
from __future__ import annotations

import io
from typing import Dict

import pandas as pd

_EXCEL_EXT = (".xlsx", ".xlsm", ".xls", ".xlsb", ".ods")
_CSV_EXT = (".csv", ".tsv", ".txt")

try:                                  # calamine reads xlsx/xls/xlsb/ods ~9x
    import python_calamine  # noqa: F401  faster than openpyxl; verified to
    _ENGINES = ("calamine", "openpyxl")  # produce identical pipeline output
except ImportError:                      # on the reference workbooks
    _ENGINES = ("openpyxl",)


def read_upload(content: bytes, filename: str) -> Dict[str, pd.DataFrame]:
    """Return ``{sheet_name: raw DataFrame}`` for the uploaded bytes.

    Excel files yield one entry per sheet; CSV/TSV yield a single entry keyed
    by the sheet-less filename stem. Every frame is read ``header=None`` and
    ``dtype=object`` so nothing is coerced or dropped — except TRAILING
    all-blank rows/columns (Excel used-range bloat: styling can inflate a
    96-row sheet to 65k rows). Trimming the tail keeps every remaining cell
    at its exact (row, col), so positional identity and A1 refs stay true.
    """
    name = (filename or "").lower().strip()

    if name.endswith(_EXCEL_EXT):
        sheets = _read_excel(content)
    elif name.endswith(_CSV_EXT):
        sheets = _read_csv(content, name)
    else:
        # Unknown extension: try Excel first (it self-validates via magic
        # bytes), then fall back to CSV. Fail honest if neither works.
        try:
            sheets = _read_excel(content)
        except Exception:
            sheets = _read_csv(content, name or "data")
    return {k: _trim_trailing_blank(df) for k, df in sheets.items()}


def _trim_trailing_blank(df: pd.DataFrame) -> pd.DataFrame:
    """Drop trailing rows/columns that hold no content at all."""
    filled = df.notna() & df.astype(str).apply(lambda c: c.str.strip() != "")
    rows = filled.any(axis=1)
    cols = filled.any(axis=0)
    last_r = int(rows[rows].index[-1]) if rows.any() else -1
    last_c = int(cols[cols].index[-1]) if cols.any() else -1
    if last_r == len(df) - 1 and last_c == df.shape[1] - 1:
        return df
    return df.iloc[:last_r + 1, :last_c + 1]


def _read_excel(content: bytes) -> Dict[str, pd.DataFrame]:
    last_exc = None
    for engine in _ENGINES:
        try:
            sheets = pd.read_excel(
                io.BytesIO(content),
                sheet_name=None,      # all sheets
                header=None,          # no header interpretation
                dtype=object,         # keep values as-is
                engine=engine,
            )
            return {str(k): v for k, v in sheets.items()}
        except Exception as exc:      # fall back to the next engine
            last_exc = exc
    raise last_exc


def _read_csv(content: bytes, name: str) -> Dict[str, pd.DataFrame]:
    sep = "\t" if name.endswith(".tsv") else None  # None -> sniff
    df = pd.read_csv(
        io.BytesIO(content),
        header=None,
        dtype=object,
        sep=sep,
        engine="python",      # needed for sep sniffing / ragged rows
        encoding="utf-8-sig",  # strip a UTF-8 BOM instead of leaking it into A1
        skip_blank_lines=False,
    )
    stem = name.rsplit("/", 1)[-1]
    for ext in _CSV_EXT:
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return {stem or "data": df}
