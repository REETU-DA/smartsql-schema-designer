"""Deterministic PostgreSQL DDL generation from reviewed schema decisions."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .import_planner import plan_import

ALLOWED_EXACT_TYPES = {
    "TEXT", "VARCHAR", "CHAR", "SMALLINT", "INTEGER", "INT", "BIGINT", "REAL",
    "DOUBLE PRECISION", "NUMERIC", "DECIMAL", "BOOLEAN", "DATE", "TIMESTAMP",
    "TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP WITH TIME ZONE", "TIME",
    "TIME WITHOUT TIME ZONE", "TIME WITH TIME ZONE", "UUID", "JSON", "JSONB", "BYTEA",
}


def quote_identifier(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("SQL identifiers cannot be empty.")
    if "\x00" in identifier:
        raise ValueError("SQL identifiers cannot contain NUL bytes.")
    return '"' + identifier.replace('"', '""') + '"'


def validate_sql_type(sql_type: str) -> str:
    value = re.sub(r"\s+", " ", str(sql_type).strip().upper())
    if value in ALLOWED_EXACT_TYPES:
        return value
    if re.fullmatch(r"(?:NUMERIC|DECIMAL)\(\d{1,2}\s*,\s*\d{1,2}\)", value):
        match = re.fullmatch(r"(?:NUMERIC|DECIMAL)\((\d+)\s*,\s*(\d+)\)", value)
        precision, scale = int(match.group(1)), int(match.group(2))
        if 1 <= precision <= 38 and 0 <= scale <= precision:
            return value
    if re.fullmatch(r"(?:NUMERIC|DECIMAL)\(\d{1,2}\)", value):
        precision = int(re.search(r"\((\d+)\)", value).group(1))
        if 1 <= precision <= 38:
            return value
    if re.fullmatch(r"VARCHAR\(\d{1,5}\)", value) or re.fullmatch(r"CHAR\(\d{1,5}\)", value):
        size = int(re.search(r"\((\d+)\)", value).group(1))
        if 1 <= size <= 65535:
            return value
    raise ValueError(f"Unsupported or unsafe PostgreSQL type {sql_type!r}. Choose a type from the type-inference options.")



def _type_group(sql_type: str) -> str:
    value = validate_sql_type(sql_type)
    if value in {"TEXT", "VARCHAR", "CHAR"} or value.startswith(("VARCHAR(", "CHAR(")):
        return "TEXT"
    if value in {"SMALLINT", "INTEGER", "INT", "BIGINT"}:
        return "INTEGER"
    if value.startswith(("NUMERIC", "DECIMAL", "REAL", "DOUBLE PRECISION")):
        return "NUMERIC"
    return value

def _constraint_name(prefix: str, body: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", f"{prefix}_{body}").strip("_").lower()
    if len(raw) <= 58:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{raw[:49]}_{digest}"


def _target_column(table: dict[str, Any], source_name: str) -> dict[str, Any] | None:
    return next((col for col in table["columns"] if col.get("included", True) and col["source_name"] == source_name), None)


def generate_postgres_sql(tables: list[dict[str, Any]], relationships: list[dict[str, Any]], *, schema_name: str | None = None) -> str:
    """Generate CREATE TABLE plus post-creation FK constraints; no data is loaded."""
    included_tables = [table for table in tables if any(col.get("included", True) for col in table["columns"])]
    if not included_tables:
        raise ValueError("No tables with included columns are available for SQL generation.")
    seen_tables: dict[str, str] = {}
    for table in included_tables:
        if not str(table.get("name", "")).strip():
            raise ValueError(f"Table {table.get('id', '')!r} has an empty target name.")
        key = str(table["name"]).casefold()
        if key in seen_tables:
            raise ValueError(f"Target table names {seen_tables[key]!r} and {table['name']!r} conflict case-insensitively.")
        seen_tables[key] = table["name"]
        seen_columns: dict[str, str] = {}
        for col in table["columns"]:
            if not col.get("included", True):
                continue
            if not str(col.get("name", "")).strip():
                raise ValueError(f"Table {table['name']!r} contains a column with an empty target name.")
            col_key = str(col["name"]).casefold()
            if col_key in seen_columns:
                raise ValueError(f"Target column names {seen_columns[col_key]!r} and {col['name']!r} in table {table['name']!r} conflict case-insensitively.")
            seen_columns[col_key] = col["name"]
    ids = {table["id"] for table in included_tables}
    if schema_name and not schema_name.strip():
        schema_name = None
    schema_prefix = f"{quote_identifier(schema_name)}." if schema_name else ""
    plan = plan_import(included_tables, [rel for rel in relationships if rel.get("approved")])
    by_id = {table["id"]: table for table in included_tables}
    lines = [
        "-- SmartSQL Schema Designer · PostgreSQL DDL",
        "-- Generated from reviewed source metadata. This creates tables only; it does not load data.",
        "-- Review validation_report.csv before importing source rows.",
        "",
    ]
    if schema_name and schema_name.casefold() != "public":
        lines.extend([f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema_name)};", ""])
    table_order = [item["table_id"] for item in plan["creation_order"] if item["table_id"] in by_id]
    for table_id in table_order:
        table = by_id[table_id]
        active = [col for col in table["columns"] if col.get("included", True)]
        if not active:
            continue
        table_name = f"{schema_prefix}{quote_identifier(table['name'])}"
        lines.append(f"CREATE TABLE {table_name} (")
        column_lines: list[str] = []
        for col in active:
            sql_type = validate_sql_type(col.get("sql_type", "TEXT"))
            line = f"    {quote_identifier(col['name'])} {sql_type}"
            if not col.get("nullable", True) and not col.get("primary_key"):
                line += " NOT NULL"
            column_lines.append(line)
        pk_cols = [col for col in active if col.get("primary_key")]
        if pk_cols:
            pk_name = _constraint_name("pk", table["name"])
            cols = ", ".join(quote_identifier(col["name"]) for col in pk_cols)
            column_lines.append(f"    CONSTRAINT {quote_identifier(pk_name)} PRIMARY KEY ({cols})")
        for col in active:
            if col.get("unique") and not col.get("primary_key"):
                uq_name = _constraint_name("uq", f"{table['name']}_{col['name']}")
                column_lines.append(f"    CONSTRAINT {quote_identifier(uq_name)} UNIQUE ({quote_identifier(col['name'])})")
        lines.append(",\n".join(column_lines))
        lines.append(");")
        lines.append("")

    approved = [rel for rel in relationships if rel.get("approved")]
    valid_approved = []
    for rel in approved:
        if rel["child_table_id"] not in ids or rel["parent_table_id"] not in ids:
            continue
        child = by_id[rel["child_table_id"]]
        parent = by_id[rel["parent_table_id"]]
        child_col = _target_column(child, rel["child_column_source"])
        parent_col = _target_column(parent, rel["parent_column_source"])
        if not child_col or not parent_col:
            raise ValueError(f"Approved relationship {rel.get('id', '')} references an excluded or unmapped column.")
        if rel.get("unmatched_row_count", 0) > 0 or not rel.get("valid_for_fk", False):
            raise ValueError(f"Foreign key {child['name']}.{child_col['name']} → {parent['name']}.{parent_col['name']} has unmatched references and cannot be safely generated.")
        parent_key = [col for col in parent["columns"] if col.get("included", True) and col.get("primary_key")]
        parent_is_single_pk = len(parent_key) == 1 and parent_key[0]["source_name"] == parent_col["source_name"]
        if not parent_is_single_pk and not parent_col.get("unique"):
            raise ValueError(f"Parent column {parent['name']}.{parent_col['name']} is not a single-column PRIMARY KEY or UNIQUE column. A column that is only part of a composite key cannot be referenced by this single-column FK implementation.")
        if _type_group(child_col.get("sql_type", "TEXT")) != _type_group(parent_col.get("sql_type", "TEXT")):
            raise ValueError(f"Foreign-key columns {child['name']}.{child_col['name']} and {parent['name']}.{parent_col['name']} use incompatible SQL type families.")
        valid_approved.append((rel, child, child_col, parent, parent_col))

    if valid_approved:
        lines.append("-- Foreign keys are added after all tables exist, so cyclic references are supported.")
        for rel, child, child_col, parent, parent_col in valid_approved:
            fk_name = _constraint_name("fk", f"{child['name']}_{child_col['name']}_{parent['name']}_{parent_col['name']}")
            child_target = f"{schema_prefix}{quote_identifier(child['name'])}"
            parent_target = f"{schema_prefix}{quote_identifier(parent['name'])}"
            lines.append(
                f"ALTER TABLE {child_target} ADD CONSTRAINT {quote_identifier(fk_name)} "
                f"FOREIGN KEY ({quote_identifier(child_col['name'])}) REFERENCES {parent_target} ({quote_identifier(parent_col['name'])});"
            )
        lines.append("")
    if plan["cycle_detected"]:
        lines.append(f"-- Dependency cycle detected among: {', '.join(plan['cyclic_tables'])}. Foreign keys are created after all tables.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
