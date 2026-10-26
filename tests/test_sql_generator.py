import pytest

from modules.file_reader import read_uploaded_file
from modules.schema_designer import build_initial_schema
from modules.sql_generator import generate_postgres_sql, quote_identifier, validate_sql_type


def test_postgres_sql_contains_pk_and_approved_fk():
    parent = read_uploaded_file("students.csv", b"student_id,name\nS1,Ada\nS2,Lin\n")[0]
    child = read_uploaded_file("registrations.csv", b"registration_id,student_id\nR1,S1\nR2,S2\n")[0]
    tables = build_initial_schema([parent, child])
    parent_table, child_table = tables
    rel = {
        "id": "r1", "child_table_id": child_table["id"], "child_column_source": "student_id",
        "parent_table_id": parent_table["id"], "parent_column_source": "student_id",
        "approved": True, "valid_for_fk": True, "unmatched_row_count": 0,
    }
    sql = generate_postgres_sql(tables, [rel])
    assert 'CREATE TABLE "students"' in sql
    assert "PRIMARY KEY" in sql
    assert "ALTER TABLE" in sql
    assert "FOREIGN KEY" in sql
    assert "ON DELETE CASCADE" not in sql


def test_reserved_keyword_and_quotes_are_escaped():
    assert quote_identifier('order') == '"order"'
    assert quote_identifier('a"b') == '"a""b"'
    dataset = read_uploaded_file("order.csv", b"order,select\n1,hello\n")[0]
    tables = build_initial_schema([dataset])
    sql = generate_postgres_sql(tables, [])
    assert '"order"' in sql
    assert '"select"' in sql


def test_invalid_sql_type_is_rejected_not_injected():
    with pytest.raises(ValueError):
        validate_sql_type("TEXT); DROP TABLE students; --")


def test_numeric_precision_is_checked():
    assert validate_sql_type("numeric(12,2)") == "NUMERIC(12,2)"
    with pytest.raises(ValueError):
        validate_sql_type("NUMERIC(3,4)")


def test_cyclic_fks_are_added_after_create_statements():
    a = read_uploaded_file("a.csv", b"a_id,b_id\nA1,B1\n")[0]
    b = read_uploaded_file("b.csv", b"b_id,a_id\nB1,A1\n")[0]
    tables = build_initial_schema([a, b])
    # Explicitly select the business keys that make this a cyclic dependency.
    for col in tables[0]["columns"]:
        col["primary_key"] = col["source_name"] == "a_id"
    for col in tables[1]["columns"]:
        col["primary_key"] = col["source_name"] == "b_id"
    rels = [
        {"id":"ab", "child_table_id": tables[0]["id"], "child_column_source":"b_id", "parent_table_id": tables[1]["id"], "parent_column_source":"b_id", "approved":True, "valid_for_fk":True, "unmatched_row_count":0},
        {"id":"ba", "child_table_id": tables[1]["id"], "child_column_source":"a_id", "parent_table_id": tables[0]["id"], "parent_column_source":"a_id", "approved":True, "valid_for_fk":True, "unmatched_row_count":0},
    ]
    sql = generate_postgres_sql(tables, rels)
    assert sql.index("CREATE TABLE") < sql.index("ALTER TABLE")
    assert sql.count("ALTER TABLE") == 2


def test_custom_schema_is_created_before_target_tables():
    dataset = read_uploaded_file("simple.csv", b"id,name\n1,Ada\n")[0]
    tables = build_initial_schema([dataset])
    sql = generate_postgres_sql(tables, [], schema_name="analytics")
    assert 'CREATE SCHEMA IF NOT EXISTS "analytics";' in sql
    assert sql.index("CREATE SCHEMA") < sql.index("CREATE TABLE")


def test_foreign_key_with_incompatible_sql_type_families_is_rejected():
    parent = read_uploaded_file("parents.csv", b"id,name\n1,Ada\n2,Lin\n")[0]
    child = read_uploaded_file("children.csv", b"child_id,parent_id\n10,1\n11,2\n")[0]
    tables = build_initial_schema([parent, child])
    parent_table, child_table = tables
    parent_id = next(col for col in parent_table["columns"] if col["source_name"] == "id")
    parent_id["primary_key"] = True
    child_fk = next(col for col in child_table["columns"] if col["source_name"] == "parent_id")
    child_fk["sql_type"] = "DATE"
    rel = {"id":"r", "child_table_id":child_table["id"], "child_column_source":"parent_id", "parent_table_id":parent_table["id"], "parent_column_source":"id", "approved":True, "valid_for_fk":True, "unmatched_row_count":0}
    with pytest.raises(ValueError, match="incompatible SQL type families"):
        generate_postgres_sql(tables, [rel])
