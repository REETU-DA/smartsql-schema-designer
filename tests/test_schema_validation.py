from modules.file_reader import read_uploaded_file
from modules.schema_designer import build_initial_schema
from modules.schema_validator import validate_schema


def test_primary_key_duplicates_and_nulls_are_located():
    dataset = read_uploaded_file("people.csv", b"person_id,fee\nP001,10.00\nP001,Pending\n,12.50\nP002,15.00\nP003,18.00\n")[0]
    tables = build_initial_schema([dataset])
    table = tables[0]
    for col in table["columns"]:
        col["primary_key"] = col["source_name"] == "person_id"
    issues = validate_schema([dataset], tables, [])
    codes = {issue["code"] for issue in issues}
    assert "primary_key_duplicate" in codes
    assert "primary_key_missing" in codes
    assert "type_exceptions" in codes or "type_mismatch" in codes
    assert all("source_row" in issue for issue in issues)


def test_ambiguous_date_is_reported_if_date_type_is_selected():
    dataset = read_uploaded_file("dates.csv", b"record_id,admission_date\n1,04/05/2025\n2,06/07/2025\n")[0]
    tables = build_initial_schema([dataset])
    table = tables[0]
    date_col = next(col for col in table["columns"] if col["source_name"] == "admission_date")
    date_col["sql_type"] = "DATE"
    issues = validate_schema([dataset], tables, [])
    assert any(issue["code"] == "ambiguous_date" for issue in issues)


def test_approved_fk_with_orphan_is_reported():
    parent = read_uploaded_file("parent.csv", b"id,name\nP1,A\n")[0]
    child = read_uploaded_file("child.csv", b"child_id,parent_id\nC1,P9\n")[0]
    tables = build_initial_schema([parent, child])
    parent_table, child_table = tables
    rel = {
        "id": "test_fk", "child_table_id": child_table["id"], "child_table": child_table["name"],
        "child_column_source": "parent_id", "child_column": "parent_id", "parent_table_id": parent_table["id"],
        "parent_table": parent_table["name"], "parent_column_source": "id", "parent_column": "id",
        "approved": True, "valid_for_fk": True, "unmatched_row_count": 0,
    }
    issues = validate_schema([parent, child], tables, [rel])
    assert any(issue["code"] == "foreign_key_unmatched" and issue["value"] == "P9" for issue in issues)


def test_unapproved_relationship_orphans_still_appear_as_warnings():
    parent = read_uploaded_file("students.csv", b"student_id,name\nS001,Ada\n")[0]
    child = read_uploaded_file("registrations.csv", b"registration_id,student_id\nR001,S999\n")[0]
    tables = build_initial_schema([parent, child])
    rel = {
        "id": "candidate", "child_table_id": tables[1]["id"], "child_table": tables[1]["name"],
        "child_column_source": "student_id", "child_column": "student_id", "parent_table_id": tables[0]["id"],
        "parent_table": tables[0]["name"], "parent_column_source": "student_id", "parent_column": "student_id",
        "approved": False, "valid_for_fk": False, "unmatched_row_count": 1,
        "unmatched_values": [{"value": "S999", "source_row": 2}],
    }
    issues = validate_schema([parent, child], tables, [rel])
    assert any(issue["code"] == "candidate_fk_unmatched" and issue["value"] == "S999" and issue["severity"] == "WARNING" for issue in issues)


def test_varchar_length_overflow_is_reported():
    dataset = read_uploaded_file("people.csv", b"person_id,name\nP001,Alexander\nP002,Lin\n")[0]
    tables = build_initial_schema([dataset])
    table = tables[0]
    name_col = next(column for column in table["columns"] if column["source_name"] == "name")
    name_col["sql_type"] = "VARCHAR(3)"
    issues = validate_schema([dataset], tables, [])
    assert any(issue["code"] == "type_mismatch" and "VARCHAR(3)" in issue["message"] for issue in issues)


def test_numeric_precision_detects_integer_overflow_even_with_trailing_zeroes():
    from modules.schema_validator import _type_check

    assert _type_check("999.99", "NUMERIC(5,2)")[0] is True
    valid, message = _type_check("1000.00", "NUMERIC(5,2)")
    assert valid is False
    assert "precision" in message.lower()
    assert _type_check("1000.00", "NUMERIC(6,2)")[0] is True


def test_numeric_precision_detects_scale_loss():
    from modules.schema_validator import _type_check

    valid, message = _type_check("1.234", "NUMERIC(8,2)")
    assert valid is False
    assert "rounding may lose precision" in message
