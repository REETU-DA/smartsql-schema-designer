"""Candidate primary-key and composite-key detection with visible evidence."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .data_profiler import clean_series

IDENTIFIER_HINTS = {"id", "identifier", "key", "code", "uuid", "guid", "number", "no"}


def _identifier_name_bonus(column: str) -> float:
    tokens = [x for x in re.split(r"[^a-z0-9]+", column.casefold()) if x]
    if any(token in IDENTIFIER_HINTS for token in tokens):
        return 0.18
    if column.casefold().endswith("id") or column.casefold().endswith("_key"):
        return 0.18
    return 0.0


def detect_candidate_keys(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Inspect individual and pairwise keys. Composite candidates are suggestions only."""
    candidates: list[dict[str, Any]] = []
    per_column: dict[str, dict[str, Any]] = {}
    row_count = len(frame)
    for column in frame.columns:
        values = clean_series(frame[column])
        missing_count = int(values.isna().sum())
        present = values.dropna()
        distinct_count = int(present.nunique())
        duplicate_count = int(present.duplicated(keep="first").sum())
        uniqueness = (distinct_count / len(present)) if len(present) else 0.0
        valid = row_count > 0 and missing_count == 0 and duplicate_count == 0 and len(present) == row_count
        score = uniqueness + _identifier_name_bonus(str(column)) - (missing_count / row_count if row_count else 1.0) * 0.5
        item = {
            "columns": [str(column)],
            "kind": "single-column",
            "unique_pct": round(uniqueness * 100, 2),
            "distinct_count": distinct_count,
            "missing_count": missing_count,
            "duplicate_count": duplicate_count,
            "valid_primary_key": bool(valid),
            "score": round(score, 3),
            "evidence": f"{distinct_count} distinct non-missing values, {missing_count} missing, {duplicate_count} repeated values.",
        }
        per_column[str(column)] = item
        candidates.append(item)

    # Composite candidate search is bounded to the first 30 columns to avoid O(n^2) on very wide files.
    composite_columns = list(frame.columns[:30])
    for i, left in enumerate(composite_columns):
        for right in composite_columns[i + 1 :]:
            if per_column[str(left)]["valid_primary_key"] or per_column[str(right)]["valid_primary_key"]:
                continue
            pair = pd.DataFrame({"left": frame[left], "right": frame[right]})
            pair_values = pair.astype("string").fillna("<NULL-SENTINEL>")
            missing_count = int(clean_series(frame[left]).isna().sum() + clean_series(frame[right]).isna().sum())
            distinct_count = int(len(pair_values.drop_duplicates()))
            valid = row_count > 0 and missing_count == 0 and distinct_count == row_count
            if valid:
                candidates.append({
                    "columns": [str(left), str(right)],
                    "kind": "composite",
                    "unique_pct": 100.0,
                    "distinct_count": distinct_count,
                    "missing_count": 0,
                    "duplicate_count": 0,
                    "valid_primary_key": True,
                    "score": round(1.0 + (_identifier_name_bonus(str(left)) + _identifier_name_bonus(str(right))) / 2, 3),
                    "evidence": f"The combination ({left}, {right}) is unique and neither column contains missing values. Review business meaning before approval.",
                })

    candidates.sort(key=lambda c: (not c["valid_primary_key"], -c["score"], len(c["columns"]), c["columns"]))
    return candidates


def best_primary_key(frame: pd.DataFrame) -> dict[str, Any] | None:
    candidates = [item for item in detect_candidate_keys(frame) if item["valid_primary_key"]]
    # Sample uniqueness is necessary but not proof of business identity. Only
    # preselect a recommendation when names provide identifier/key evidence;
    # other unique fields stay visible for manual review instead of being
    # silently promoted to a primary key.
    semantic_candidates = [
        item for item in candidates
        if any(_identifier_name_bonus(column) > 0 for column in item["columns"])
    ]
    if not semantic_candidates:
        return None
    semantic_candidates.sort(key=lambda c: (len(c["columns"]) != 1, -c["score"], c["columns"]))
    return semantic_candidates[0]
