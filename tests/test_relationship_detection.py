from modules.file_reader import read_uploaded_file
from modules.relationship_detection import detect_relationships, many_to_many_patterns
from modules.schema_designer import build_initial_schema


def _datasets():
    students = read_uploaded_file("students.csv", b"Student_ID,Name\nS001,Ada\nS002,Lin\n")[0]
    enrollments = read_uploaded_file("enrollments.csv", b"Enrollment_ID,Student_ID,Course_ID\nE001,S001,C001\nE002,S001,C002\nE003,S002,C001\n")[0]
    courses = read_uploaded_file("courses.csv", b"Course_ID,Title\nC001,SQL\nC002,BI\n")[0]
    return [students, enrollments, courses]


def test_foreign_key_candidates_use_overlap_and_names():
    datasets = _datasets()
    tables = build_initial_schema(datasets)
    relationships = detect_relationships(datasets, tables)
    student_fk = next(rel for rel in relationships if rel["child_table"] == "enrollments" and rel["child_column_source"] == "Student_ID" and rel["parent_table"] == "students")
    assert student_fk["distinct_value_overlap_pct"] == 100.0
    assert student_fk["unmatched_row_count"] == 0
    assert student_fk["valid_for_fk"] is True
    assert student_fk["relationship_type"] == "one-to-many"


def test_unmatched_foreign_key_reference_is_reported():
    students = read_uploaded_file("students.csv", b"Student_ID,Name\nS001,Ada\n")[0]
    enrollment = read_uploaded_file("enrollments.csv", b"Enrollment_ID,Student_ID\nE001,S999\n")[0]
    datasets = [students, enrollment]
    tables = build_initial_schema(datasets)
    relationships = detect_relationships(datasets, tables)
    candidate = next(rel for rel in relationships if rel["child_table"] == "enrollments" and rel["parent_table"] == "students")
    assert candidate["valid_for_fk"] is False
    assert candidate["unmatched_row_count"] == 1
    assert candidate["unmatched_values"][0]["value"] == "S999"


def test_many_to_many_junction_pattern_is_surfaced():
    datasets = _datasets()
    tables = build_initial_schema(datasets)
    relationships = detect_relationships(datasets, tables)
    patterns = many_to_many_patterns(relationships)
    assert any(pattern["junction_table"] == "enrollments" and pattern["left_table"] != pattern["right_table"] for pattern in patterns)


def test_differently_named_identifier_columns_can_match_by_values():
    parent = read_uploaded_file("customers.csv", b"customer_code,name\nC001,Ada\nC002,Lin\n")[0]
    child = read_uploaded_file("transactions.csv", b"transaction_id,client_ref\nT001,C001\nT002,C002\n")[0]
    datasets = [parent, child]
    tables = build_initial_schema(datasets)
    relationships = detect_relationships(datasets, tables)
    candidate = next(rel for rel in relationships if rel["child_table"] == "transactions" and rel["child_column_source"] == "client_ref" and rel["parent_table"] == "customers")
    assert candidate["distinct_value_overlap_pct"] == 100.0
    assert candidate["valid_for_fk"] is True
    assert candidate["confidence"] in {"Medium", "High"}
