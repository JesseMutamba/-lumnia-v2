"""Step 6: automated EDA on tidy extracted tables.

Facts first, claims second: ``run_eda`` computes bounded exploratory facts
(missingness, duplicates, constants, skew, IQR outliers, correlations,
categorical breakdowns); ``generate_insights`` turns them into ranked
narrative insights — and only makes a claim when the evidence clears a floor
(``MIN_ROWS_FOR_CLAIMS``): a correlation on a handful of rows is noise, and
reporting it confidently is exactly the failure mode Lumnia exists to avoid.

EDA runs at extraction time on the FULL columns (``run_eda_df`` from
``orient._extract_tidy_table``), never on the 8-row record previews stored in
reports.
"""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

MAX_NUMERIC_COLS = 15
MAX_SAMPLE_ROWS = 10_000
MAX_CATEGORICAL_CARDINALITY = 20
SKEW_LOG_THRESHOLD = 1.0
OUTLIER_IQR_MULTIPLIER = 1.5
# Below this many rows, correlation/categorical claims are noise: the facts
# are still computed and returned, but no insight is asserted from them.
MIN_ROWS_FOR_CLAIMS = 10

# French first — the reference workbooks are French — then English.
TARGET_NAME_PATTERNS = re.compile(
    r"(montant|prix|co[uû]t|salaire|revenu|somme|valeur|budget|d[eé]pense"
    r"|price|total|amount|revenue|sales|value|cost|income|profit|balance|payment)",
    re.IGNORECASE,
)

_NULLISH = {"", "none", "nan", "null"}


def _sample_df(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) <= MAX_SAMPLE_ROWS:
        return df
    return df.sample(n=MAX_SAMPLE_ROWS, random_state=42)


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    cols: list[str] = []
    for col in df.columns:
        series = pd.to_numeric(df[col], errors="coerce")
        if series.notna().sum() >= max(3, len(df) * 0.05):
            cols.append(col)
    return cols[:MAX_NUMERIC_COLS]


def _detect_target(numeric_cols: list[str]) -> str | None:
    for col in numeric_cols:
        if TARGET_NAME_PATTERNS.search(str(col)):
            return col
    if numeric_cols:
        return numeric_cols[-1]
    return None


def _skewness(series: pd.Series) -> float | None:
    clean = series.dropna()
    if len(clean) < 3:
        return None
    return float(clean.skew())


def _iqr_outlier_count(series: pd.Series) -> int:
    clean = series.dropna()
    if len(clean) < 4:
        return 0
    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return 0
    lower = q1 - OUTLIER_IQR_MULTIPLIER * iqr
    upper = q3 + OUTLIER_IQR_MULTIPLIER * iqr
    return int(((clean < lower) | (clean > upper)).sum())


def _correlations(df: pd.DataFrame, numeric_cols: list[str]) -> dict[str, dict[str, float]]:
    if len(numeric_cols) < 2:
        return {}
    num_df = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    corr = num_df.corr(numeric_only=True, min_periods=3)
    result: dict[str, dict[str, float]] = {}
    for col in corr.columns:
        pairs: dict[str, float] = {}
        for other, val in corr[col].items():
            if col == other or pd.isna(val):
                continue
            pairs[str(other)] = round(float(val), 4)
        if pairs:
            result[str(col)] = pairs
    return result


def _top_drivers(
    df: pd.DataFrame, correlations: dict[str, dict[str, float]],
    target: str, limit: int = 5
) -> list[dict[str, Any]]:
    if target not in correlations:
        return []
    tnum = pd.to_numeric(df[target], errors="coerce")
    drivers = []
    for col, r in correlations[target].items():
        if col == target:
            continue
        n_pairs = int((pd.to_numeric(df[col], errors="coerce").notna()
                       & tnum.notna()).sum())
        drivers.append({"column": col, "correlation": r,
                        "abs_correlation": abs(r), "n_pairs": n_pairs})
    drivers.sort(key=lambda d: d["abs_correlation"], reverse=True)
    return drivers[:limit]


def _categorical_breakdowns(
    df: pd.DataFrame, target: str | None, numeric_cols: list[str]
) -> list[dict[str, Any]]:
    if target is None or target not in df.columns:
        return []

    target_series = pd.to_numeric(df[target], errors="coerce")
    breakdowns: list[dict[str, Any]] = []

    for col in df.columns:
        if col == target or col in numeric_cols:
            continue
        series = df[col].astype(str)
        # blank-ish categories are missing data, not a category
        series = series[~series.str.strip().str.lower().isin(_NULLISH)]
        nunique = series.nunique(dropna=True)
        if nunique == 0 or nunique > MAX_CATEGORICAL_CARDINALITY:
            continue

        grouped = (
            pd.DataFrame({"cat": series, "target": target_series})
            .dropna(subset=["cat", "target"])
            .groupby("cat")["target"]
            .agg(["mean", "median", "count"])
            .reset_index()
            .sort_values("mean", ascending=False)
        )

        categories = []
        for _, row in grouped.head(10).iterrows():
            categories.append(
                {
                    "category": str(row["cat"]),
                    "mean": round(float(row["mean"]), 4),
                    "median": round(float(row["median"]), 4),
                    "count": int(row["count"]),
                }
            )

        if categories:
            breakdowns.append(
                {
                    "column": str(col),
                    "cardinality": int(nunique),
                    "categories": categories,
                }
            )

    return breakdowns


def _safe_float(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def run_eda(records: list[dict[str, Any]], sheet_name: str = "") -> dict[str, Any]:
    """Run bounded EDA on a list of tidy record dicts."""
    if not records:
        return _empty(sheet_name)
    return run_eda_df(pd.DataFrame(records), sheet_name)


def _empty(sheet_name: str) -> dict[str, Any]:
    return {
        "sheet": sheet_name,
        "row_count": 0,
        "column_count": 0,
        "missingness": {},
        "duplicates": 0,
        "constant_columns": [],
        "numeric_columns": {},
        "target_column": None,
        "correlations": {},
        "top_drivers": [],
        "categorical_breakdowns": [],
    }


def run_eda_df(df: pd.DataFrame, sheet_name: str = "") -> dict[str, Any]:
    """Same as :func:`run_eda`, on a DataFrame (the extraction-time path with
    the FULL columns — never the stored 8-row preview)."""
    if df.empty:
        return _empty(sheet_name)

    df = _sample_df(df)
    row_count = len(df)

    missingness: dict[str, dict[str, Any]] = {}
    for col in df.columns:
        null_count = int(df[col].isna().sum()
                         + (df[col].astype(str).str.strip() == "").sum())
        missingness[str(col)] = {
            "count": null_count,
            "pct": round(100.0 * null_count / row_count, 2) if row_count else 0.0,
        }

    duplicates = int(df.duplicated().sum())

    constant_columns = [
        str(col) for col in df.columns if df[col].nunique(dropna=True) <= 1
    ]

    numeric_cols = _numeric_columns(df)
    target = _detect_target(numeric_cols)

    numeric_summary: dict[str, dict[str, Any]] = {}
    for col in numeric_cols:
        series = pd.to_numeric(df[col], errors="coerce")
        skew = _skewness(series)
        outliers = _iqr_outlier_count(series)
        numeric_summary[col] = {
            "min": _safe_float(series.min()),
            "max": _safe_float(series.max()),
            "mean": _safe_float(series.mean()),
            "median": _safe_float(series.median()),
            "std": _safe_float(series.std()),
            "skewness": round(skew, 4) if skew is not None else None,
            "outlier_count": outliers,
            "outlier_pct": round(100.0 * outliers / row_count, 2) if row_count else 0.0,
            "suggest_log_transform": skew is not None and skew >= SKEW_LOG_THRESHOLD,
        }

    correlations = _correlations(df, numeric_cols)
    top_drivers = _top_drivers(df, correlations, target) if target else []
    categorical_breakdowns = _categorical_breakdowns(df, target, numeric_cols)

    return {
        "sheet": sheet_name,
        "row_count": row_count,
        "column_count": len(df.columns),
        "missingness": missingness,
        "duplicates": duplicates,
        "constant_columns": constant_columns,
        "numeric_columns": numeric_summary,
        "target_column": target,
        "correlations": correlations,
        "top_drivers": top_drivers,
        "categorical_breakdowns": categorical_breakdowns,
    }


def generate_insights(eda_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn EDA facts into ranked narrative insights.

    Claims that need statistical support (drivers, categorical contrasts)
    are only asserted when the table has at least ``MIN_ROWS_FOR_CLAIMS``
    rows — the facts stay available either way, the *narrative* stays honest.
    """
    insights: list[dict[str, Any]] = []

    for eda in eda_results:
        sheet = eda.get("sheet") or "sheet"
        row_count = eda.get("row_count", 0)
        target = eda.get("target_column")
        enough_rows = row_count >= MIN_ROWS_FOR_CLAIMS

        missing_here = []
        for col, info in eda.get("missingness", {}).items():
            pct = info.get("pct", 0)
            # fully-empty columns are layout artifacts (spacers, sparse
            # bands), and a percentage over a handful of rows is not a signal
            if not (5 <= pct < 100 and row_count >= 5):
                continue
            if pct >= 80:
                # near-empty columns in operational sheets are usually
                # structural (future periods not yet filled) — note, not alarm
                sev = "low"
                msg = (f"'{col}' is mostly empty ({pct:.0f}%) — likely not yet "
                       f"filled in or structural.")
                msg_fr = (f"« {col} » est presque vide ({pct:.0f} %) — "
                          f"probablement pas encore rempli, ou structurel.")
            else:
                sev = "high" if pct >= 30 else "medium" if pct >= 15 else "low"
                msg = (f"{pct:.1f}% of '{col}' values are missing "
                       f"({info['count']} of {row_count} rows).")
                msg_fr = (f"{pct:.1f} % des valeurs de « {col} » sont "
                          f"manquantes ({info['count']} lignes sur {row_count}).")
            missing_here.append(
                {
                    "type": "missingness",
                    "severity": sev,
                    "sheet": sheet,
                    "column": col,
                    "message": msg,
                    "message_fr": msg_fr,
                    "evidence": info,
                    # partial gaps are the interesting ones: score peaks
                    # mid-band instead of at 100%
                    "score": pct if pct < 80 else 100 - pct,
                }
            )
        missing_here.sort(key=lambda i: i["score"], reverse=True)
        insights.extend(missing_here[:3])        # cap per sheet

        dupes = eda.get("duplicates", 0)
        if dupes > 0:
            insights.append(
                {
                    "type": "duplicates",
                    "severity": "medium" if dupes > 5 else "low",
                    "sheet": sheet,
                    "message": f"{dupes} duplicate row(s) detected in '{sheet}'.",
                    "message_fr": f"{dupes} ligne(s) en double détectée(s) dans « {sheet} ».",
                    "evidence": {"count": dupes},
                    "score": dupes,
                }
            )

        for col, info in eda.get("numeric_columns", {}).items():
            if info.get("suggest_log_transform") and enough_rows:
                insights.append(
                    {
                        "type": "distribution",
                        "severity": "low",
                        "sheet": sheet,
                        "column": col,
                        "message": (
                            f"'{col}' is strongly right-skewed "
                            f"(skewness={info['skewness']}) — a few large values "
                            f"dominate; averages will mislead."
                        ),
                        "message_fr": (
                            f"« {col} » est très asymétrique "
                            f"(asymétrie = {info['skewness']}) — quelques grandes "
                            f"valeurs dominent ; les moyennes seront trompeuses."
                        ),
                        "evidence": info,
                        "score": abs(info.get("skewness") or 0),
                    }
                )
            outlier_pct = info.get("outlier_pct", 0)
            if outlier_pct >= 1 and enough_rows:
                insights.append(
                    {
                        "type": "outliers",
                        "severity": "medium" if outlier_pct >= 5 else "low",
                        "sheet": sheet,
                        "column": col,
                        "message": (
                            f"'{col}' has {info['outlier_count']} unusual value(s) "
                            f"({outlier_pct:.1f}% of rows) — worth a look before "
                            f"trusting totals."
                        ),
                        "message_fr": (
                            f"« {col} » contient {info['outlier_count']} valeur(s) "
                            f"atypique(s) ({outlier_pct:.1f} % des lignes) — à "
                            f"vérifier avant de se fier aux totaux."
                        ),
                        "evidence": info,
                        "score": outlier_pct,
                    }
                )

        for driver in eda.get("top_drivers", []):
            # |r| ~ 1 on financial tables means a derived or duplicated
            # column (Montant = Qte x Prix is the checks engine's job), and
            # any claim needs enough rows behind it
            if (target and driver.get("n_pairs", 0) >= MIN_ROWS_FOR_CLAIMS
                    and 0.3 <= abs(driver.get("correlation", 0)) <= 0.995):
                r = driver["correlation"]
                insights.append(
                    {
                        "type": "driver",
                        "severity": "high" if abs(r) >= 0.7 else "medium",
                        "sheet": sheet,
                        "column": driver["column"],
                        "target": target,
                        "message": (
                            f"'{driver['column']}' moves with '{target}' "
                            f"(r={r:.2f} over {driver['n_pairs']} rows)."
                        ),
                        "message_fr": (
                            f"« {driver['column']} » évolue avec « {target} » "
                            f"(r = {r:.2f} sur {driver['n_pairs']} lignes)."
                        ),
                        "evidence": driver,
                        "score": abs(r) * 100,
                    }
                )

        for bd in eda.get("categorical_breakdowns", []):
            cats = bd.get("categories", [])
            if cats and target and enough_rows and cats[0].get("count", 0) >= 3:
                top = cats[0]
                insights.append(
                    {
                        "type": "categorical",
                        "severity": "medium",
                        "sheet": sheet,
                        "column": bd["column"],
                        "target": target,
                        "message": (
                            f"'{bd['column']}' varies by category: "
                            f"'{top['category']}' has the highest mean {target} "
                            f"({top['mean']:.2f}, n={top['count']})."
                        ),
                        "message_fr": (
                            f"« {bd['column']} » varie selon la catégorie : "
                            f"« {top['category']} » a la moyenne de {target} la "
                            f"plus élevée ({top['mean']:.2f}, n = {top['count']})."
                        ),
                        "evidence": bd,
                        "score": 50,
                    }
                )

    severity_rank = {"high": 3, "medium": 2, "low": 1}
    insights.sort(
        key=lambda i: (severity_rank.get(i["severity"], 0), i.get("score", 0)),
        reverse=True,
    )
    return insights[:25]
