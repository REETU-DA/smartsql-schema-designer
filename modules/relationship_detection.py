"""Evidence-based FK and cardinality candidate detection across datasets."""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

import pandas as pd

from .data_profiler import clean_series, source_row_number
from .key_detection import detect_candidate_keys


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _tokens(value: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", str(value).casefold()) if part}


def _name_similarity(child_column: str, parent_column: str, parent_table: str) -> tuple[float, str]:
    child = _compact(child_column)
    parent = _compact(parent_column)
    child_tokens = _tokens(child_column)
    parent_tokens = _tokens(parent_column)
    table_tokens = _tokens(parent_table)
    if child == parent:
        return 1.0, "Child and parent column names match after normalisation."
    if child_tokens & table_tokens and ("id" in child_tokens or child.endswith("id")):
        return 0.92, "Child column includes the parent table's name and an identifier hint."
    if parent_tokens <= child_tokens and ("id" in child_tokens or child.endswith("id")):
        return 0.88, "Child identifier name contains the parent key's name."
    ratio = SequenceMatcher(None, child, parent).ratio()
    token_overlap = len(child_tokens & parent_tokens) / max(1, len(child_tokens | parent_tokens))
    score = max(ratio, token_overlap)
    explanation = "Column names are similar." if score >= 0.55 else "Column names provide weak evidence only."
    return score, explanation


def _canonical(value: Any) -> str:
    text = str(value).strip()
    # Canonicalise plain numeric values without removing textual leading zeros.
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)", text) and not re.fullmatch(r"0\d+", text.lstrip("+-")):
        try:
            number = float(text)
            if number.is_integer():
                return str(int(number))
        except ValueError:
            pass
    return text


def detect_relationships(datasets: list[Any], tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data_by_id = {dataset.dataset_id: dataset for dataset in datasets}
    by_id = {table["id"]: table for table in tables}
    candidates: list[dict[str, Any]] = []
    for child_table in tables:
        child_ds = data_by_id[child_table["id"]]
        child_frame = child_ds.frame
        for child_col in child_table["columns"]:
            if not child_col.get("included", True):
                continue
            child_source = child_col["source_name"]
            if child_source not in child_frame:
                continue
            child_values = clean_series(child_frame[child_source]).dropna()
            if child_values.empty:
                continue
            child_value_keys = child_values.map(_canonical)
            child_unique_keys = set(child_value_keys.tolist())
            for parent_table in tables:
                if parent_table["id"] == child_table["id"]:
                    continue
                parent_ds = data_by_id[parent_table["id"]]
                parent_frame = parent_ds.frame
                parent_columns = [c for c in parent_table["columns"] if c.get("included", True)]
                parent_key_columns = [c for c in parent_columns if c.get("primary_key")]
                # Include other observed candidate keys as possible parents, but keep the evidence explicit.
                valid_key_names = {
                    candidate["columns"][0]
                    for candidate in detect_candidate_keys(parent_frame)
                    if candidate["valid_primary_key"] and len(candidate["columns"]) == 1
                }
                potential_parents = [c for c in parent_columns if c["source_name"] in valid_key_names]
                for parent_col in potential_parents:
                    parent_source = parent_col["source_name"]
                    if parent_source not in parent_frame:
                        continue
                    parent_values = clean_series(parent_frame[parent_source]).dropna()
                    parent_keys = parent_values.map(_canonical)
                    if parent_keys.empty or parent_keys.duplicated().any():
                        continue
                    name_score, name_reason = _name_similarity(child_source, parent_source, parent_table["name"])
                    parent_set = set(parent_keys.tolist())
                    matching_keys = child_unique_keys & parent_set
                    overlap_ratio = len(matching_keys) / len(child_unique_keys) if child_unique_keys else 0.0
                    matched_rows_mask = child_value_keys.isin(parent_set)
                    matched_rows = int(matched_rows_mask.sum())
                    total_non_null_rows = int(len(child_value_keys))
                    row_coverage = matched_rows / total_non_null_rows if total_non_null_rows else 0.0
                    # Names can vary across operational systems (for example customer_code vs
                    # client_ref). Strong value overlap may still justify a lower-confidence
                    # candidate when both fields look identifier-like, but overlap alone is not
                    # enough to invent a relationship between arbitrary text columns.
                    identifier_hints = {"id", "identifier", "key", "code", "ref", "reference", "uuid", "guid", "no", "number"}
                    child_identifier_like = bool(_tokens(child_source) & identifier_hints) or _compact(child_source).endswith(("id", "key", "code", "ref"))
                    parent_identifier_like = bool(_tokens(parent_source) & identifier_hints) or _compact(parent_source).endswith(("id", "key", "code", "ref"))
                    strong_value_evidence = overlap_ratio >= 0.90 and child_identifier_like and parent_identifier_like
                    if (name_score < 0.55 and not strong_value_evidence) or (overlap_ratio == 0 and name_score < 0.80):
                        continue
                    unmatched_rows: list[dict[str, Any]] = []
                    unmatched_value_keys = set(child_value_keys[~matched_rows_mask].tolist())
                    for idx, value in child_values.items():
                        if _canonical(value) in unmatched_value_keys:
                            unmatched_rows.append({
                                "value": str(value),
                                "source_row": source_row_number(child_ds, int(idx)),
                            })
                    # Keep exact value/source-row evidence and don't silently coerce identifiers.
                    unique_unmatched = []
                    seen_unmatched = set()
                    for entry in unmatched_rows:
                        identity = (entry["value"], entry["source_row"])
                        if identity not in seen_unmatched:
                            seen_unmatched.add(identity)
                            unique_unmatched.append(entry)
                    score = 0.42 * name_score + 0.50 * row_coverage + 0.08
                    confidence = "High" if score >= 0.88 and row_coverage >= 0.90 else "Medium" if score >= 0.65 else "Low"
                    child_duplicates = bool(child_value_keys.duplicated().any())
                    observed_cardinality = "one-to-many" if child_duplicates else "observed one-to-one; confirm business rule"
                    valid = len(unique_unmatched) == 0
                    rel_id = f"{child_table['id']}::{child_source}->{parent_table['id']}::{parent_source}"
                    candidates.append({
                        "id": rel_id,
                        "child_table_id": child_table["id"],
                        "child_table": child_table["name"],
                        "child_column_source": child_source,
                        "child_column": child_col["name"],
                        "parent_table_id": parent_table["id"],
                        "parent_table": parent_table["name"],
                        "parent_column_source": parent_source,
                        "parent_column": parent_col["name"],
                        "name_similarity": round(name_score, 3),
                        "distinct_value_overlap_pct": round(overlap_ratio * 100, 2),
                        "matching_non_null_rows_pct": round(row_coverage * 100, 2),
                        "child_non_null_rows": total_non_null_rows,
                        "matched_rows": matched_rows,
                        "unmatched_row_count": len(unique_unmatched),
                        "unmatched_values": unique_unmatched[:500],
                        "parent_key_unique": True,
                        "parent_key_missing_count": int(clean_series(parent_frame[parent_source]).isna().sum()),
                        "relationship_type": observed_cardinality,
                        "confidence": confidence,
                        "score": round(score, 3),
                        "valid_for_fk": valid,
                        "approved": False,
                        "evidence": f"{name_reason} {len(matching_keys)}/{len(child_unique_keys)} distinct child values match ({overlap_ratio:.1%}); {matched_rows}/{total_non_null_rows} non-missing child rows match ({row_coverage:.1%}). Parent values are unique and non-missing for the candidate key.",
                        "warning": "" if valid else f"{len(unique_unmatched)} child source row(s) contain values absent from the parent key. The FK cannot be safely enforced until those references are resolved.",
                    })
    # Retain only the strongest three parent candidates per child-column to keep review actionable.
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in candidates:
        grouped.setdefault((item["child_table_id"], item["child_column_source"]), []).append(item)
    output: list[dict[str, Any]] = []
    for group in grouped.values():
        group.sort(key=lambda item: (-item["score"], item["unmatched_row_count"], item["parent_table"]))
        output.extend(group[:3])
    return output


def many_to_many_patterns(relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect junction-table patterns from evidence; recommendations are not constraints."""
    by_child: dict[str, list[dict[str, Any]]] = {}
    for rel in relationships:
        if rel["valid_for_fk"] and rel["matching_non_null_rows_pct"] >= 80:
            by_child.setdefault(rel["child_table_id"], []).append(rel)
    patterns: list[dict[str, Any]] = []
    for table_id, rels in by_child.items():
        # Repetition in both child FK columns is evidence for an associative pattern.
        rels = [rel for rel in rels if rel.get("relationship_type") == "one-to-many"]
        parents: dict[str, dict[str, Any]] = {}
        for rel in rels:
            parents.setdefault(rel["parent_table_id"], rel)
        if len(parents) < 2:
            continue
        selected = list(parents.values())
        for i, left in enumerate(selected):
            for right in selected[i + 1 :]:
                patterns.append({
                    "junction_table_id": table_id,
                    "junction_table": left["child_table"],
                    "left_table": left["parent_table"],
                    "left_column": left["child_column"],
                    "right_table": right["parent_table"],
                    "right_column": right["child_column"],
                    "confidence": "Medium",
                    "evidence": "The same child dataset contains FK candidates to both parent tables. Confirm that the business domain permits multiple links before treating this as an M:N relationship.",
                })
    return patterns
