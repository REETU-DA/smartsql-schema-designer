"""Pre-import checks tied to source rows and the currently reviewed schema."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import date, datetime
import re
from typing import Any

import pandas as pd

from .data_profiler import clean_series, is_missing, source_row_number

NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
AMBIGUOUS_SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _issue(severity: str, code: str, dataset: Any, table: dict[str, Any] | None, column: str | None,
           row: int | None, value: Any, message: str, recommendation: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "source_file": getattr(dataset, "source_file", "") if dataset is not None else "",
        "source_sheet": getattr(dataset, "source_sheet", None) if dataset is not None else None,
        "table": table.get("name", "") if table else "",
        "column": column or "",
        "source_row": row if row is not None else "",
        "value": "" if is_missing(value) else str(value)[:500],
        "message": message,
        "recommendation": recommendation,
    }


def _canonical_key(value: Any) -> str:
    text = str(value).strip()
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)", text) and not re.fullmatch(r"0\d+", text.lstrip("+-")):
        try:
            number = Decimal(text)
            if number == number.to_integral_value():
                return str(int(number))
            return format(number.normalize(), "f")
        except InvalidOperation:
            pass
    return text


def _type_check(value: Any, sql_type: str) -> tuple[bool, str | None]:
    text = str(value).strip()
    upper = re.sub(r"\s+", " ", sql_type.strip().upper())
    if upper in {"TEXT", "VARCHAR", "CHAR", "JSON", "JSONB", "BYTEA"}:
        return True, None
    length_match = re.fullmatch(r"(?:VARCHAR|CHAR)\((\d+)\)", upper)
    if length_match:
        max_length = int(length_match.group(1))
        if len(text) > max_length:
            return False, f"Value contains {len(text)} characters but {upper} permits only {max_length}; truncation or import failure is possible."
        return True, None
    if upper in {"SMALLINT", "INTEGER", "INT", "BIGINT"}:
        if not re.fullmatch(r"[+-]?\d+", text):
            return False, "Value is not an integer."
        number = int(text)
        bounds = {"SMALLINT": (-32768, 32767), "INTEGER": (-2147483648, 2147483647), "INT": (-2147483648, 2147483647), "BIGINT": (-9223372036854775808, 9223372036854775807)}
        low, high = bounds[upper]
        return (True, None) if low <= number <= high else (False, f"Value exceeds the {upper} range.")
    if upper in {"NUMERIC", "DECIMAL"} or upper.startswith(("NUMERIC(", "DECIMAL(", "REAL", "DOUBLE PRECISION")):
        cleaned = text.replace(",", "")
        if not NUMERIC_RE.fullmatch(cleaned):
            return False, "Value is not a valid number."
        decimal_value = Decimal(cleaned)
        match = re.match(r"(?:NUMERIC|DECIMAL)\((\d+)\s*,\s*(\d+)\)", upper)
        if match:
            precision, scale = int(match.group(1)), int(match.group(2))
            # Keep the original exponent when checking fractional precision, and
            # derive integer digits from the magnitude. Decimal.normalize() would
            # strip trailing zeros (e.g. 1000.00) and can under-report overflow.
            decimal_tuple = decimal_value.as_tuple()
            actual_scale = max(0, -decimal_tuple.exponent)
            integer_digits = max(0, decimal_value.copy_abs().adjusted() + 1) if decimal_value else 0
            if actual_scale > scale:
                return False, f"Value has {actual_scale} fractional digits but the selected type permits {scale}; rounding may lose precision."
            if integer_digits + scale > precision:
                return False, f"Value exceeds NUMERIC({precision},{scale}) precision."
        return True, None
    if upper == "BOOLEAN":
        return (True, None) if text.casefold() in {"true", "false", "t", "f", "yes", "no", "y", "n", "1", "0"} else (False, "Value is not a recognised Boolean.")
    if upper == "DATE":
        match = AMBIGUOUS_SLASH_DATE.fullmatch(text)
        if match:
            first, second = int(match.group(1)), int(match.group(2))
            if first <= 12 and second <= 12 and first != second:
                return False, "Date is ambiguous: it could be day/month or month/day. Choose the source format before converting."
        try:
            pd.to_datetime(text, errors="raise", format="mixed")
            return True, None
        except Exception:
            return False, "Value is not a valid date."
    if upper.startswith("TIMESTAMP"):
        try:
            pd.to_datetime(text, errors="raise", format="mixed")
            return True, None
        except Exception:
            return False, "Value is not a valid timestamp."
    if upper.startswith("TIME"):
        try:
            pd.to_timedelta(text)
            return True, None
        except Exception:
            return False, "Value is not a valid time value."
    if upper == "UUID":
        import uuid
        try:
            uuid.UUID(text)
            return True, None
        except ValueError:
            return False, "Value is not a valid UUID."
    return False, f"Unsupported or unsafe SQL type declaration: {sql_type!r}."


def validate_schema(datasets: list[Any], tables: list[dict[str, Any]], relationships: list[dict[str, Any]], *, max_issues: int = 20000) -> list[dict[str, Any]]:
    data_by_id = {dataset.dataset_id: dataset for dataset in datasets}
    issues: list[dict[str, Any]] = []

    def add(item: dict[str, Any]) -> None:
        if len(issues) < max_issues:
            issues.append(item)

    table_names: dict[str, str] = {}
    for table in tables:
        if not str(table.get("name", "")).strip():
            ds = data_by_id.get(table["id"])
            add(_issue("ERROR", "empty_table_name", ds, table, "", None, table.get("name", ""), "Target table name is empty.", "Enter a non-empty target table name."))
        normalized = str(table.get("name", "")).casefold()
        if normalized in table_names:
            ds = data_by_id.get(table["id"])
            add(_issue("ERROR", "duplicate_table_name", ds, table, "", None, table["name"],
                       f"Table name {table['name']!r} conflicts with {table_names[normalized]!r} (case-insensitive).",
                       "Give each target table a unique name."))
        table_names[normalized] = table["name"]
        dataset = data_by_id.get(table["id"])
        if dataset is None:
            continue
        frame = dataset.frame
        active = [col for col in table["columns"] if col.get("included", True)]
        if not active:
            add(_issue("ERROR", "empty_target_table", dataset, table, "", None, "", "No columns are selected for this table.", "Include at least one column or remove the table."))
        names_seen: dict[str, str] = {}
        for col in active:
            name_key = col["name"].casefold()
            if name_key in names_seen:
                add(_issue("ERROR", "duplicate_column_name", dataset, table, col["name"], None, col["name"],
                           f"Target column name conflicts with {names_seen[name_key]!r} (case-insensitive).",
                           "Use unique target column names."))
            names_seen[name_key] = col["name"]
            source = col["source_name"]
            if source not in frame.columns:
                add(_issue("ERROR", "missing_source_column", dataset, table, col["name"], None, source,
                           "The mapped source column no longer exists.", "Restore the source mapping or exclude this column."))
                continue
            series = clean_series(frame[source])
            selected_type = str(col.get("sql_type", "TEXT"))
            inference = col.get("inference", {})
            if inference.get("ambiguous_date"):
                add(_issue("WARNING", "ambiguous_date", dataset, table, col["name"], None, ", ".join(inference.get("exceptions", [])[:5]),
                           "Date-like values have more than one plausible day/month interpretation.",
                           "Keep TEXT or explicitly choose the source date format before changing to DATE."))
            if inference.get("exceptions") and not inference.get("ambiguous_date"):
                add(_issue("WARNING", "type_exceptions", dataset, table, col["name"], None, ", ".join(map(str, inference["exceptions"][:5])),
                           "Some observed values do not fit the strongest inferred type.",
                           "Review the exception values and either clean them or select a type that preserves them."))
            # Missing values are reported for every column, but nullable fields are informational.
            missing_indices = [int(i) for i, value in series.items() if is_missing(value)]
            required = bool(col.get("primary_key") or not col.get("nullable", True))
            if missing_indices:
                severity = "ERROR" if required else "INFO"
                code = "required_value_missing" if required else "missing_value"
                for idx in missing_indices[:500]:
                    add(_issue(severity, code, dataset, table, col["name"], source_row_number(dataset, idx), "",
                               "Required field is missing." if required else "Source field is missing; target column currently allows NULL.",
                               "Provide a value or allow NULL according to the intended business rule."))
                if len(missing_indices) > 500:
                    add(_issue("WARNING", "issue_rows_truncated", dataset, table, col["name"], None, len(missing_indices),
                               f"Only the first 500 missing-value rows are listed; {len(missing_indices)} rows are affected.",
                               "Filter the source data for missing values to inspect the complete affected-row set."))
            # Leading zeros warn when a chosen numeric SQL type could discard identifier formatting.
            if selected_type.upper() not in {"TEXT", "VARCHAR", "CHAR", "UUID"} and ("id" in source.casefold() or "code" in source.casefold() or "postal" in source.casefold() or "zip" in source.casefold()):
                zero_values = [(idx, value) for idx, value in series.items() if not is_missing(value) and re.fullmatch(r"0\d+", str(value).strip())]
                for idx, value in zero_values[:100]:
                    add(_issue("WARNING", "leading_zero_risk", dataset, table, col["name"], source_row_number(dataset, int(idx)), value,
                               "This identifier-like value has leading zeros that a numeric type would discard.",
                               "Use TEXT/VARCHAR unless the leading zeros are known to be insignificant."))
            for idx, value in series.items():
                if is_missing(value):
                    continue
                valid, reason = _type_check(value, selected_type)
                if not valid:
                    code = "ambiguous_date" if reason and "ambiguous" in reason.lower() else "type_mismatch"
                    severity = "WARNING" if code == "ambiguous_date" else "ERROR"
                    add(_issue(severity, code, dataset, table, col["name"], source_row_number(dataset, int(idx)), value,
                               reason or "Value is incompatible with selected type.",
                               "Review the source value or change the target type; no values are changed automatically."))
            if col.get("unique") and not col.get("primary_key"):
                non_missing = series.dropna()
                duplicates = non_missing[non_missing.duplicated(keep=False)]
                for idx, value in duplicates.items():
                    add(_issue("ERROR", "unique_violation", dataset, table, col["name"], source_row_number(dataset, int(idx)), value,
                               "A UNIQUE constraint was selected, but this value occurs more than once.",
                               "Remove the UNIQUE constraint or resolve all duplicates before importing."))

        pk_columns = [col for col in active if col.get("primary_key")]
        if not pk_columns:
            add(_issue("WARNING", "no_primary_key_selected", dataset, table, "", None, "",
                       "No primary key is selected for this table. SQL can be generated, but row identity and future relationship enforcement may be unclear.",
                       "Review candidate keys and select a valid existing key or a business-supported composite key. A surrogate key is not added automatically."))
            # When the table has no approved key, show row-level failures in columns
            # whose names imply identifiers. Duplicates in known foreign-key columns
            # of a table that already has a PK are not treated as errors.
            identifier_tokens = {"id", "identifier", "key", "uuid", "guid", "code"}
            for candidate_col in active:
                source = candidate_col["source_name"]
                tokens = {part for part in re.split(r"[^a-z0-9]+", source.casefold()) if part}
                identifier_like = bool(tokens & identifier_tokens) or source.casefold().endswith(("id", "key", "code"))
                if not identifier_like or source not in frame.columns:
                    continue
                candidate_values = clean_series(frame[source])
                missing_candidate_rows = [int(i) for i, value in candidate_values.items() if is_missing(value)]
                repeated_mask = candidate_values.notna() & candidate_values.duplicated(keep=False)
                repeated_rows = [int(i) for i in frame.index[repeated_mask].tolist()]
                for idx in missing_candidate_rows[:200]:
                    add(_issue("WARNING", "candidate_key_missing_value", dataset, table, candidate_col["name"], source_row_number(dataset, idx), "",
                               "Potential identifier column contains a missing value while no primary key is selected.",
                               "Review whether this column is intended to identify records; fill values or choose a different key."))
                for idx in repeated_rows[:500]:
                    add(_issue("WARNING", "candidate_key_duplicate_value", dataset, table, candidate_col["name"], source_row_number(dataset, idx), frame.loc[idx, source],
                               "Potential identifier column contains a repeated value while no primary key is selected.",
                               "Review duplicates and confirm whether this is an identifier, a foreign key or an ordinary attribute."))
        if pk_columns:
            if any(col["source_name"] not in frame.columns for col in pk_columns):
                continue
            pk_frame = pd.DataFrame({col["source_name"]: clean_series(frame[col["source_name"]]) for col in pk_columns})
            missing_mask = pk_frame.isna().any(axis=1)
            for idx in pk_frame.index[missing_mask].tolist()[:500]:
                values = {key: (None if is_missing(value) else str(value)) for key, value in pk_frame.loc[idx].to_dict().items()}
                add(_issue("ERROR", "primary_key_missing", dataset, table, ", ".join(col["name"] for col in pk_columns), source_row_number(dataset, int(idx)), values,
                           "Primary-key columns must be non-missing for every row.", "Repair the identifier or choose a different key. A generated surrogate key is not created automatically."))
            duplicate_mask = pk_frame.astype("string").fillna("<NULL>").duplicated(keep=False)
            for idx in pk_frame.index[duplicate_mask].tolist()[:1000]:
                values = {key: (None if is_missing(value) else str(value)) for key, value in pk_frame.loc[idx].to_dict().items()}
                add(_issue("ERROR", "primary_key_duplicate", dataset, table, ", ".join(col["name"] for col in pk_columns), source_row_number(dataset, int(idx)), values,
                           "The selected primary-key value (or composite combination) is duplicated.", "Resolve duplicates or select a valid single/composite key."))

        frame_for_duplicates = frame.astype("string")
        duplicate_mask = frame_for_duplicates.duplicated(keep=False) if len(frame_for_duplicates) else pd.Series(dtype=bool)
        for idx in frame.index[duplicate_mask].tolist()[:500]:
            add(_issue("WARNING", "duplicate_record", dataset, table, "", source_row_number(dataset, int(idx)), "",
                       "This entire source row is duplicated.", "Confirm whether duplicate records are intentional before import."))

    # Report orphan references for every detected candidate. Unapproved candidates are
    # warnings; an explicitly approved relationship with orphans is an error.
    for rel in relationships:
        child_table = next((t for t in tables if t["id"] == rel["child_table_id"]), None)
        parent_table = next((t for t in tables if t["id"] == rel["parent_table_id"]), None)
        child_ds = data_by_id.get(rel["child_table_id"])
        parent_ds = data_by_id.get(rel["parent_table_id"])
        if not child_table or not parent_table or not child_ds or not parent_ds:
            continue
        child_source, parent_source = rel["child_column_source"], rel["parent_column_source"]
        if child_source not in child_ds.frame or parent_source not in parent_ds.frame:
            add(_issue("ERROR" if rel.get("approved") else "WARNING", "fk_mapping_missing", child_ds, child_table, rel.get("child_column", ""), None, "",
                       "Relationship mapping refers to a missing source column.", "Review the relationship mapping."))
            continue
        if not rel.get("approved") and rel.get("unmatched_row_count", 0) > 0:
            code = "candidate_fk_unmatched"
            for entry in rel.get("unmatched_values", [])[:500]:
                add(_issue("WARNING", code, child_ds, child_table, rel.get("child_column", child_source), entry.get("source_row"), entry.get("value", ""),
                           f"Candidate reference is absent from {parent_table['name']}.{rel.get('parent_column', parent_source)}; this candidate has not been approved as a constraint.",
                           "Verify that the datasets are related, then repair the value or leave the FK unapproved."))
            if len(rel.get("unmatched_values", [])) < int(rel.get("unmatched_row_count", 0)):
                add(_issue("WARNING", "candidate_fk_unmatched_truncated", child_ds, child_table, rel.get("child_column", child_source), None, rel.get("unmatched_row_count", 0),
                           "The unmatched-reference details are capped at 500 rows for this candidate.", "Inspect the relationship report for the listed values and repair the full source set."))
            continue
        parent_values = {_canonical_key(v) for v in clean_series(parent_ds.frame[parent_source]).dropna().tolist()}
        for idx, value in clean_series(child_ds.frame[child_source]).items():
            if is_missing(value):
                continue
            if _canonical_key(value) not in parent_values:
                severity = "ERROR" if rel.get("approved") else "WARNING"
                code = "foreign_key_unmatched" if rel.get("approved") else "candidate_fk_unmatched"
                add(_issue(severity, code, child_ds, child_table, rel.get("child_column", child_source), source_row_number(child_ds, int(idx)), value,
                           f"No matching value exists in {parent_table['name']}.{rel.get('parent_column', parent_source)}.",
                           "Add the missing parent record, correct the source key, or do not enforce this FK until resolved."))

    if len(issues) >= max_issues:
        issues.append({"severity": "WARNING", "code": "global_issue_limit", "source_file": "", "source_sheet": "", "table": "", "column": "", "source_row": "", "value": "", "message": f"Issue list reached the configured cap of {max_issues} entries; additional issues may not be listed.", "recommendation": "Resolve high-severity issues and rerun validation with a higher limit if needed."})
    return issues
