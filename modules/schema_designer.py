"""Initial schema proposal builder. Does not silently split source datasets."""
from __future__ import annotations

import re
from typing import Any

from .key_detection import best_primary_key, detect_candidate_keys
from .type_inference import infer_sql_type


def to_snake_case(value: str, fallback: str = "column") -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value).strip())
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    if not value:
        value = fallback
    if value[0].isdigit():
        value = f"t_{value}"
    return value


def _unique_name(name: str, taken: set[str]) -> str:
    base = to_snake_case(name, "table")
    candidate = base
    counter = 2
    while candidate.casefold() in taken:
        candidate = f"{base}_{counter}"
        counter += 1
    taken.add(candidate.casefold())
    return candidate


def build_initial_schema(datasets: list[Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    used_table_names: set[str] = set()
    for dataset in datasets:
        frame = dataset.frame
        table_name_source = dataset.source_sheet or dataset.source_file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        table_name = _unique_name(table_name_source, used_table_names)
        key_candidates = detect_candidate_keys(frame)
        best_key = best_primary_key(frame)
        columns: list[dict[str, Any]] = []
        used_column_names: set[str] = set()
        for source_name in frame.columns:
            inferred = infer_sql_type(str(source_name), frame[source_name])
            target_name = _unique_name(str(source_name), used_column_names)
            columns.append({
                "source_name": str(source_name),
                "name": target_name,
                "sql_type": inferred["sql_type"],
                # Existing non-null data is not proof that future rows must be non-null.
                "nullable": True,
                "primary_key": False,
                "unique": False,
                "included": True,
                "inference": inferred,
            })
        if best_key:
            for column in columns:
                if column["source_name"] in best_key["columns"]:
                    column["primary_key"] = True
        table = {
            "id": dataset.dataset_id,
            "name": table_name,
            "source_file": dataset.source_file,
            "source_sheet": dataset.source_sheet,
            "file_format": dataset.file_format,
            "row_count": len(frame),
            "columns": columns,
            "key_candidates": key_candidates,
            "recommended_pk": best_key,
        }
        tables.append(table)
    return tables
