import pandas as pd

from modules.type_inference import infer_sql_type


def test_identifier_preserves_leading_zeros():
    result = infer_sql_type("postal_code", pd.Series(["00125", "00126"]))
    assert result["sql_type"] == "TEXT"


def test_integer_column():
    result = infer_sql_type("quantity", pd.Series(["1", "2", "100"]))
    assert result["sql_type"] == "INTEGER"
    assert result["confidence"] == "High"


def test_decimal_fee_column():
    result = infer_sql_type("fee", pd.Series(["99.95", "105.50", "0.25"]))
    assert result["sql_type"].startswith("NUMERIC(")


def test_ambiguous_slash_dates_are_not_silently_converted():
    result = infer_sql_type("admission_date", pd.Series(["04/05/2025", "06/07/2025"]))
    assert result["sql_type"] == "TEXT"
    assert result["ambiguous_date"] is True
    assert "04/05/2025" in result["exceptions"]


def test_mixed_numeric_values_keep_exception_evidence():
    result = infer_sql_type("Fee", pd.Series(["99.00", "120.00", "Pending", "89.00", "10.00"]))
    assert result["sql_type"].startswith("NUMERIC(")
    assert "Pending" in result["exceptions"]


def test_boolean_labels():
    result = infer_sql_type("is_active", pd.Series(["Yes", "No", "yes"]))
    assert result["sql_type"] == "BOOLEAN"


def test_whole_number_monetary_column_reserves_decimal_scale():
    result = infer_sql_type("Fee", pd.Series(["100", "200", "1250"]))
    assert result["sql_type"].startswith("NUMERIC(")
    assert result["sql_type"].endswith(",2)")
