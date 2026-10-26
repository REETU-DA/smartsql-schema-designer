"""Generate machine-readable schema, validation, mapping and ERD outputs."""
from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pandas as pd

from .data_profiler import profile_dataset
from .erd_generator import generate_erd_svg
from .import_planner import plan_import
from .normalization_analysis import analyse_normalization_opportunities
from .sql_generator import generate_postgres_sql


def _csv_bytes(rows: list[dict[str, Any]], columns: list[str] | None = None) -> bytes:
    frame = pd.DataFrame(rows, columns=columns)
    return frame.to_csv(index=False).encode("utf-8-sig")


def generate_report_bundle(
    datasets: list[Any],
    tables: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    *,
    design_mode: str = "Direct Import Schema",
    schema_name: str | None = None,
) -> dict[str, bytes]:
    data_by_id = {dataset.dataset_id: dataset for dataset in datasets}
    profiles = [profile_dataset(dataset) for dataset in datasets]
    normalization_opportunities = analyse_normalization_opportunities(datasets, tables)
    plan = plan_import(tables, relationships)
    sql = generate_postgres_sql(tables, relationships, schema_name=schema_name)
    schema_obj = {
        "application": "SmartSQL Schema Designer",
        "version": "0.1.0",
        "target_dialect": "PostgreSQL",
        "target_schema": schema_name or "search_path",
        "design_mode": design_mode,
        "source_processing": "Local/in-memory analysis; schema generation does not load data or connect to a database.",
        "tables": tables,
        "relationships": relationships,
        "profiles": profiles,
        "normalization_opportunities": normalization_opportunities,
        "import_plan": plan,
        "validation_summary": {
            "issue_count": len(issues),
            "errors": sum(issue.get("severity") == "ERROR" for issue in issues),
            "warnings": sum(issue.get("severity") == "WARNING" for issue in issues),
            "info": sum(issue.get("severity") == "INFO" for issue in issues),
        },
        "limitations": [
            "PostgreSQL DDL is the only fully implemented SQL dialect in this version.",
            "Relational mode surfaces relationships and normalization clues but does not automatically decompose source datasets; source columns remain traceable.",
            "Foreign-key suggestions are evidence-based candidates and must be explicitly approved in the interface.",
        ],
    }
    schema_report_json = json.dumps(schema_obj, ensure_ascii=False, indent=2, default=str).encode("utf-8")

    schema_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    for table in tables:
        dataset = data_by_id.get(table["id"])
        for column in table["columns"]:
            inference = column.get("inference", {})
            common = {
                "table_id": table["id"],
                "source_file": table["source_file"],
                "source_sheet": table.get("source_sheet") or "",
                "source_column": column["source_name"],
                "target_table": table["name"],
                "target_column": column["name"],
                "sql_type": column["sql_type"],
                "included": bool(column.get("included", True)),
                "nullable": bool(column.get("nullable", True)) and not bool(column.get("primary_key")),
                "primary_key": bool(column.get("primary_key")),
                "unique": bool(column.get("unique")),
                "confidence": inference.get("confidence", "Unknown"),
                "inference_reason": inference.get("reason", ""),
                "exceptions": json.dumps(inference.get("exceptions", []), ensure_ascii=False),
            }
            schema_rows.append(common)
            mapping_rows.append({
                "source_file": table["source_file"],
                "source_sheet": table.get("source_sheet") or "",
                "source_dataset_id": table["id"],
                "source_column": column["source_name"],
                "target_table": table["name"],
                "target_column": column["name"],
                "sql_type": column["sql_type"],
                "included": bool(column.get("included", True)),
            })

    relationship_rows = []
    for rel in relationships:
        relationship_rows.append({key: (json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value) for key, value in rel.items()})

    readme = """SmartSQL Schema Designer — generated PostgreSQL schema outputs

1. Review schema.sql and validation_report.csv before importing source rows.
2. The SQL file creates database tables and approved constraints only; it does not connect to a database or load data.
3. Open psql or your preferred PostgreSQL client and execute the script in the intended database.
4. For a local file from the psql client, use client-side \\copy, e.g.:
   \\copy public.students(student_id, student_name) FROM '/path/to/students.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');
5. SQL COPY reads a path visible to the PostgreSQL server process; psql \\copy reads a path visible to the client process.
6. Resolve missing/duplicate keys and unmatched foreign-key references before loading constrained tables.
7. Load parent tables before child tables. If a selected relationship cycle exists, load according to your data strategy and add constraints only after references are valid.
8. No database connection, credentials, source-file upload or data-loading operation was performed by this application.

Limitations in version 0.1.0:
- PostgreSQL is the only fully implemented DDL dialect.
- Relational mode does not automatically split source datasets into normalised tables; it preserves source mapping and surfaces evidence for review.
- Foreign keys are suggestions until explicitly approved. No cascading deletes/updates are generated.
"""
    schema_columns = ["table_id", "source_file", "source_sheet", "source_column", "target_table", "target_column", "sql_type", "included", "nullable", "primary_key", "unique", "confidence", "inference_reason", "exceptions"]
    relationship_columns = ["id", "child_table_id", "child_table", "child_column_source", "child_column", "parent_table_id", "parent_table", "parent_column_source", "parent_column", "name_similarity", "distinct_value_overlap_pct", "matching_non_null_rows_pct", "child_non_null_rows", "matched_rows", "unmatched_row_count", "unmatched_values", "parent_key_unique", "parent_key_missing_count", "relationship_type", "confidence", "score", "valid_for_fk", "approved", "evidence", "warning"]
    validation_columns = ["severity", "code", "source_file", "source_sheet", "table", "column", "source_row", "value", "message", "recommendation"]
    mapping_columns = ["source_file", "source_sheet", "source_dataset_id", "source_column", "target_table", "target_column", "sql_type", "included"]
    normalization_columns = ["category", "source_file", "source_sheet", "table", "column", "evidence", "suggestion", "confidence"]
    return {
        "schema.sql": sql.encode("utf-8"),
        "schema_report.json": schema_report_json,
        "schema_report.csv": _csv_bytes(schema_rows, schema_columns),
        "relationship_report.csv": _csv_bytes(relationship_rows, relationship_columns),
        "validation_report.csv": _csv_bytes(issues, validation_columns),
        "source_mapping.csv": _csv_bytes(mapping_rows, mapping_columns),
        "normalization_opportunities.csv": _csv_bytes(normalization_opportunities, normalization_columns),
        "README.txt": readme.encode("utf-8"),
        "erd.svg": generate_erd_svg(tables, relationships).encode("utf-8"),
    }
