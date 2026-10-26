"""Conservative SQL type inference based on names and observed values."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import re
import uuid
from typing import Any

import pandas as pd

from .data_profiler import is_missing

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$")
ID_TOKENS = {"id", "identifier", "key", "code", "uuid", "guid", "phone", "mobile", "telephone", "zip", "zipcode", "postal", "postcode", "account", "number"}
NUMERIC_TOKENS = {"amount", "fee", "price", "cost", "sales", "revenue", "profit", "balance", "salary", "rate", "quantity", "total", "discount", "tax"}
MONETARY_TOKENS = {"amount", "fee", "price", "cost", "sales", "revenue", "profit", "balance", "salary", "rate", "total", "discount", "tax"}
DATE_TOKENS = {"date", "dob", "birthdate", "timestamp", "created_at", "updated_at", "admission_date", "appointment_date"}


def _name_tokens(name: str) -> list[str]:
    return [part for part in re.split(r"[^a-zA-Z0-9]+|(?<=[a-z])(?=[A-Z])", str(name).lower()) if part]


def _string_values(values: list[Any]) -> list[str]:
    return [str(v).strip() for v in values if not is_missing(v)]


def _decimal_details(values: list[str]) -> tuple[int, int]:
    max_integer = 1
    max_scale = 0
    for value in values:
        try:
            dec = Decimal(value.replace(",", ""))
        except (InvalidOperation, ValueError):
            continue
        tup = dec.as_tuple()
        scale = max(0, -tup.exponent)
        digits = len(tup.digits)
        integer_digits = max(1, digits - scale)
        max_integer = max(max_integer, integer_digits)
        max_scale = max(max_scale, scale)
    precision = max(1, min(38, max_integer + max_scale))
    return precision, min(max_scale, precision - 1 if precision > 1 else 0)


def infer_sql_type(column_name: str, series: pd.Series) -> dict[str, Any]:
    """Return SQL type, confidence, evidence, exceptions and ambiguity flags."""
    raw = [v for v in series.tolist() if not is_missing(v)]
    values = _string_values(raw)
    tokens = set(_name_tokens(column_name))
    lower_name = str(column_name).lower()
    missing_count = int(len(series) - len(values))
    result: dict[str, Any] = {
        "sql_type": "TEXT",
        "reason": "Text is the safe fallback when the values do not support a stronger type confidently.",
        "confidence": "Low",
        "exceptions": [],
        "ambiguous_date": False,
        "nullable_recommended": True,
        "observed_non_null": len(values),
    }
    if not values:
        result.update(reason="No non-missing values were observed; TEXT avoids making an unsupported assumption.", confidence="Low")
        return result

    if all(UUID_RE.match(v) for v in values):
        result.update(sql_type="UUID", reason="Every non-missing value matches a UUID pattern.", confidence="High")
        return result

    # Preserve identifier-like values, especially leading zeros and phone/postal numbers.
    all_digits = all(re.fullmatch(r"[+-]?\d+", v) for v in values)
    leading_zero = any(re.fullmatch(r"0\d+", v) for v in values)
    identifier_like = bool(tokens & ID_TOKENS) or any(tok.endswith("_id") for tok in [lower_name])
    if identifier_like or leading_zero:
        reason = "Column name indicates an identifier/contact/code field; string storage preserves formatting and leading zeros." if identifier_like else "At least one value has leading zeros; text storage preserves the original identifier representation."
        result.update(sql_type="TEXT", reason=reason, confidence="High" if identifier_like or leading_zero else "Medium")
        if all_digits and leading_zero:
            result["exceptions"] = [v for v in values if re.fullmatch(r"0\d+", v)][:10]
        return result

    bool_values = {v.casefold() for v in values}
    boolean_tokens = {"true", "false", "yes", "no", "y", "n", "t", "f"}
    if bool_values <= boolean_tokens and (len(bool_values) > 1 or lower_name.startswith(("is_", "has_", "can_"))):
        result.update(sql_type="BOOLEAN", reason="Values are consistently recognised Boolean labels.", confidence="High")
        return result
    if bool_values <= {"0", "1"} and lower_name.startswith(("is_", "has_", "can_")):
        result.update(sql_type="BOOLEAN", reason="The column name suggests a flag and every value is 0 or 1.", confidence="Medium")
        return result

    if all(TIME_RE.match(v) for v in values):
        result.update(sql_type="TIME", reason="All values match a time-of-day pattern.", confidence="High")
        return result

    # Explicitly flag common slash dates that could be day/month or month/day.
    slash_dates = [v for v in values if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", v)]
    ambiguous = []
    for value in slash_dates:
        first, second, _year = [int(part) for part in value.split("/")]
        if first <= 12 and second <= 12 and first != second:
            ambiguous.append(value)
    if ambiguous:
        result.update(
            sql_type="TEXT",
            reason="Date-like values have ambiguous day/month ordering; TEXT preserves source values until the user chooses a format.",
            confidence="High",
            ambiguous_date=True,
            exceptions=ambiguous[:10],
        )
        return result

    date_like = all(ISO_DATE_RE.match(v) for v in values)
    timestamp_like = all(ISO_TIMESTAMP_RE.match(v) for v in values)
    if date_like:
        parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="coerce")
        if parsed.notna().all():
            result.update(sql_type="DATE", reason="All values are valid ISO calendar dates (YYYY-MM-DD).", confidence="High")
            return result
    if timestamp_like:
        parsed = pd.to_datetime(values, errors="coerce", utc=False)
        if parsed.notna().all():
            tz_present = any(re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", v) for v in values)
            sql_type = "TIMESTAMP WITH TIME ZONE" if tz_present else "TIMESTAMP"
            result.update(sql_type=sql_type, reason="Values consistently match ISO timestamps; timezone offsets are preserved in the selected type." if tz_present else "Values consistently match ISO timestamps.", confidence="High")
            return result

    # Less rigid date inference is allowed only for date-named columns.
    date_named = bool(tokens & DATE_TOKENS) or lower_name.endswith(("_date", "_at", "_timestamp"))
    if date_named:
        parsed = pd.to_datetime(values, errors="coerce", format="mixed")
        invalid = [v for v, p in zip(values, parsed) if pd.isna(p)]
        if not invalid and parsed.notna().any():
            # Slash dates that weren't ambiguous still have a consistent interpretation.
            tz_present = any(re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", v) for v in values)
            all_date_only = all(not re.search(r"\d{1,2}:\d{2}", v) for v in values)
            sql_type = "DATE" if all_date_only else ("TIMESTAMP WITH TIME ZONE" if tz_present else "TIMESTAMP")
            result.update(sql_type=sql_type, reason="The column name indicates a date/time and all observed values parse; verify the source format before import.", confidence="Medium")
            return result
        if invalid:
            result.update(reason="The column name suggests a date/time, but some values cannot be parsed consistently.", confidence="Low", exceptions=invalid[:10])
            return result

    # Numeric inference is value-driven, with type exceptions explicitly exposed.
    numeric = pd.to_numeric(pd.Series(values).str.replace(",", "", regex=False), errors="coerce")
    numeric_mask = numeric.notna()
    numeric_ratio = float(numeric_mask.mean()) if len(numeric) else 0.0
    if numeric_ratio == 1.0:
        converted_values = [v.replace(",", "") for v in values]
        if all(re.fullmatch(r"[+-]?\d+", v) for v in converted_values):
            ints = [int(v) for v in converted_values]
            if tokens & MONETARY_TOKENS:
                max_integer_digits = max(len(str(abs(value))) for value in ints)
                precision = min(38, max(3, max_integer_digits + 2))
                result.update(sql_type=f"NUMERIC({precision},2)", reason="The column name suggests a monetary/decimal measure; two fractional digits are reserved even though the observed sample values are whole numbers.", confidence="Medium")
            elif min(ints) >= -2147483648 and max(ints) <= 2147483647:
                result.update(sql_type="INTEGER", reason="Every value is an integer within the PostgreSQL INTEGER range.", confidence="High")
            elif min(ints) >= -9223372036854775808 and max(ints) <= 9223372036854775807:
                result.update(sql_type="BIGINT", reason="Every value is an integer requiring the PostgreSQL BIGINT range.", confidence="High")
            else:
                result.update(sql_type="NUMERIC(38,0)", reason="Integer values exceed BIGINT range; NUMERIC preserves their magnitude.", confidence="High")
            return result
        precision, scale = _decimal_details(converted_values)
        if tokens & MONETARY_TOKENS:
            integer_digits = max(1, precision - scale)
            scale = max(2, scale)
            precision = min(38, max(precision, integer_digits + scale))
        preferred = f"NUMERIC({precision},{scale})"
        result.update(sql_type=preferred, reason="Every value is numeric; NUMERIC avoids binary floating-point rounding in stored decimal values.", confidence="High")
        return result
    if numeric_ratio >= 0.8:
        exceptions = [v for v, valid in zip(values, numeric_mask.tolist()) if not valid]
        precision, scale = _decimal_details([v.replace(",", "") for v in values if re.fullmatch(r"[+-]?[\d,]+(?:\.\d+)?", v)])
        result.update(
            sql_type=f"NUMERIC({precision},{scale})",
            reason=f"Most observed values are numeric ({numeric_ratio:.0%}), but nonnumeric exceptions require cleanup or a deliberate text type.",
            confidence="Medium",
            exceptions=exceptions[:20],
        )
        return result

    # Pandas-native values (for already typed Parquet columns) need explicit recognition.
    if all(isinstance(v, (datetime, pd.Timestamp)) for v in raw):
        tz_present = any(getattr(v, "tzinfo", None) is not None for v in raw)
        result.update(sql_type="TIMESTAMP WITH TIME ZONE" if tz_present else "TIMESTAMP", reason="Source column is already timestamp-typed.", confidence="High")
        return result
    if all(isinstance(v, date) and not isinstance(v, datetime) for v in raw):
        result.update(sql_type="DATE", reason="Source column is already date-typed.", confidence="High")
        return result
    if all(isinstance(v, bool) for v in raw):
        result.update(sql_type="BOOLEAN", reason="Source values are Boolean-typed.", confidence="High")
        return result

    # If a series contains mixed native types, retain text and report non-string variation.
    native_types = {type(v).__name__ for v in raw}
    if len(native_types) > 1:
        result.update(reason=f"Mixed source value types were observed ({', '.join(sorted(native_types))}); TEXT avoids an unsafe cast.", confidence="Low", exceptions=[_safe_value(v) for v in raw if not isinstance(v, str)][:10])
    else:
        result.update(reason="Values are not consistently numeric, Boolean, date, time, or UUID; TEXT preserves their original content.", confidence="Medium")
    return result


def _safe_value(value: Any) -> str:
    try:
        return str(value)[:100]
    except Exception:
        return "<unprintable>"
