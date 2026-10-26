"""Data profiling and row-level source diagnostics."""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except (TypeError, ValueError):
        return False


def clean_series(series: pd.Series) -> pd.Series:
    return series.map(lambda value: pd.NA if is_missing(value) else value)


def _display(value: Any, limit: int = 60) -> str:
    if is_missing(value):
        return "(missing)"
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def profile_dataset(dataset: Any) -> dict[str, Any]:
    frame: pd.DataFrame = dataset.frame
    columns: list[dict[str, Any]] = []
    for name in frame.columns:
        series = clean_series(frame[name])
        non_missing = series.dropna()
        duplicates_count = int(non_missing.duplicated(keep="first").sum())
        profile = {
            "column": str(name),
            "missing_count": int(series.isna().sum()),
            "missing_pct": round(float(series.isna().mean() * 100), 2) if len(series) else 0.0,
            "distinct_count": int(non_missing.nunique(dropna=True)),
            "duplicate_values": duplicates_count,
            "constant": bool(non_missing.nunique(dropna=True) <= 1 and len(non_missing) > 0),
            "sample_values": [_display(v) for v in non_missing.drop_duplicates().head(5).tolist()],
            "min": None,
            "max": None,
            "outlier_count": 0,
        }
        numeric = pd.to_numeric(non_missing, errors="coerce")
        name_tokens = set(re.split(r"[^a-z0-9]+", str(name).casefold()))
        identifier_like = bool(name_tokens & {"id", "identifier", "key", "code", "phone", "mobile", "postal", "postcode", "zip", "uuid", "account"})
        leading_zero_like = any(re.fullmatch(r"0\d+", str(value).strip()) for value in non_missing.tolist())
        if not identifier_like and not leading_zero_like and len(numeric) and numeric.notna().mean() > 0.9:
            numeric = numeric.dropna()
            profile["min"] = float(numeric.min()) if len(numeric) else None
            profile["max"] = float(numeric.max()) if len(numeric) else None
            if len(numeric) >= 4:
                q1, q3 = numeric.quantile([0.25, 0.75])
                iqr = q3 - q1
                if iqr > 0:
                    profile["outlier_count"] = int(((numeric < q1 - 1.5 * iqr) | (numeric > q3 + 1.5 * iqr)).sum())
        columns.append(profile)

    duplicate_rows_mask = frame.astype("string").duplicated(keep=False) if len(frame) else pd.Series(dtype=bool)
    duplicate_row_count = int(duplicate_rows_mask.sum()) if len(duplicate_rows_mask) else 0
    return {
        "dataset_id": dataset.dataset_id,
        "source_file": dataset.source_file,
        "source_sheet": dataset.source_sheet,
        "file_format": dataset.file_format,
        "size_bytes": dataset.size_bytes,
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "duplicate_row_count": duplicate_row_count,
        "duplicate_row_source_numbers": [int(i) + dataset.source_record_offset for i in frame.index[duplicate_rows_mask].tolist()] if duplicate_row_count else [],
        "columns": columns,
        "warnings": list(dataset.warnings),
    }


def source_row_number(dataset: Any, dataframe_index: int) -> int:
    """Best-effort physical row / record number in the source file."""
    return int(dataframe_index) + int(getattr(dataset, "source_record_offset", 2))
