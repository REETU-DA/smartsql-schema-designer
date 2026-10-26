"""Conservative relational-design opportunities; never rewrites source datasets."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .data_profiler import clean_series, is_missing


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def analyse_normalization_opportunities(datasets: list[Any], tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return review suggestions with evidence rather than automatic decompositions."""
    data_by_id = {dataset.dataset_id: dataset for dataset in datasets}
    findings: list[dict[str, Any]] = []
    comparable_columns: dict[str, list[dict[str, Any]]] = {}

    for table in tables:
        dataset = data_by_id.get(table["id"])
        if dataset is None:
            continue
        frame: pd.DataFrame = dataset.frame
        for column in table["columns"]:
            if not column.get("included", True):
                continue
            source = column["source_name"]
            if source not in frame.columns:
                continue
            series = clean_series(frame[source])
            present = series.dropna()
            if present.empty:
                continue
            distinct_count = int(present.nunique())
            if distinct_count == 1 and len(present) >= 2:
                findings.append({
                    "category": "Constant attribute",
                    "source_file": table["source_file"],
                    "source_sheet": table.get("source_sheet") or "",
                    "table": table["name"],
                    "column": column["name"],
                    "evidence": f"The sample contains one distinct non-missing value across {len(present)} non-missing rows.",
                    "suggestion": "Check whether this is a meaningful attribute, a batch-level property, or a source artifact. Do not remove it based on this sample alone.",
                    "confidence": "Low",
                })
            text_values = [str(value) for value in present.tolist()]
            delimited_count = sum(bool(re.search(r"[,;|]", value)) for value in text_values)
            if len(text_values) >= 2 and delimited_count / len(text_values) >= 0.2:
                examples = [value for value in text_values if re.search(r"[,;|]", value)][:3]
                findings.append({
                    "category": "Possible repeating values",
                    "source_file": table["source_file"],
                    "source_sheet": table.get("source_sheet") or "",
                    "table": table["name"],
                    "column": column["name"],
                    "evidence": f"{delimited_count}/{len(text_values)} values contain a comma, semicolon or pipe. Examples: {examples}.",
                    "suggestion": "Confirm whether these are true multi-valued attributes. If so, consider a related child/junction table with one value per row and an explicit source-to-target mapping.",
                    "confidence": "Medium",
                })
            comparable_columns.setdefault(_norm_name(source), []).append({
                "table": table,
                "column": column,
                "values": {_safe_key(value) for value in present.tolist()},
            })

        # Identical columns within a source dataset can indicate redundant source fields.
        active_cols = [col for col in table["columns"] if col.get("included", True) and col["source_name"] in frame.columns]
        for index, left in enumerate(active_cols):
            left_values = clean_series(frame[left["source_name"]]).map(_safe_key).tolist()
            for right in active_cols[index + 1 :]:
                right_values = clean_series(frame[right["source_name"]]).map(_safe_key).tolist()
                if left_values == right_values and len(left_values) > 0:
                    findings.append({
                        "category": "Potential duplicate attributes",
                        "source_file": table["source_file"],
                        "source_sheet": table.get("source_sheet") or "",
                        "table": table["name"],
                        "column": f"{left['name']} / {right['name']}",
                        "evidence": "Both columns have the same value at every observed source row, including missing-value positions.",
                        "suggestion": "Confirm whether these columns represent the same concept or distinct business facts before excluding or combining either column.",
                        "confidence": "Medium",
                    })

    # Matching names across tables can indicate a shared domain attribute, but not proof of identity.
    for normalized_name, items in comparable_columns.items():
        if not normalized_name or len(items) < 2:
            continue
        for index, left in enumerate(items):
            for right in items[index + 1 :]:
                if left["table"]["id"] == right["table"]["id"]:
                    continue
                smaller = min(len(left["values"]), len(right["values"]))
                if smaller == 0:
                    continue
                overlap = len(left["values"] & right["values"]) / smaller
                if overlap >= 0.8:
                    findings.append({
                        "category": "Shared-domain candidate",
                        "source_file": f"{left['table']['source_file']} · {right['table']['source_file']}",
                        "source_sheet": f"{left['table'].get('source_sheet') or ''} · {right['table'].get('source_sheet') or ''}",
                        "table": f"{left['table']['name']} / {right['table']['name']}",
                        "column": f"{left['column']['name']} / {right['column']['name']}",
                        "evidence": f"Columns with the same normalised name share {overlap:.0%} of the smaller distinct-value set ({len(left['values'] & right['values'])} overlapping distinct values).",
                        "suggestion": "Confirm that the attributes have the same meaning and lifecycle. A shared lookup or entity may help if independent attributes or controlled values exist; do not normalise on overlap alone.",
                        "confidence": "Low" if overlap < 1 else "Medium",
                    })
    findings.sort(key=lambda item: (item["category"], item["table"], item["column"]))
    return findings


def _safe_key(value: Any) -> str:
    return "<MISSING>" if is_missing(value) else str(value).strip()
