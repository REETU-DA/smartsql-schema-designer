from modules.file_reader import read_uploaded_file
from modules.reporting import generate_report_bundle
from modules.schema_designer import build_initial_schema


def test_report_bundle_has_headers_even_when_no_issues_or_relationships():
    dataset = read_uploaded_file("simple.csv", b"id,name\n1,Ada\n2,Lin\n")[0]
    tables = build_initial_schema([dataset])
    bundle = generate_report_bundle([dataset], tables, [], [])
    assert b"severity,code,source_file" in bundle["validation_report.csv"]
    assert b"child_table_id" in bundle["relationship_report.csv"]
    assert b"source_file,source_sheet,source_dataset_id" in bundle["source_mapping.csv"]
    assert bundle["schema.sql"].startswith(b"-- SmartSQL Schema Designer")
    assert bundle["erd.svg"].startswith(b"<svg")


def test_normalization_opportunities_are_evidence_based_and_non_destructive():
    from modules.normalization_analysis import analyse_normalization_opportunities

    dataset = read_uploaded_file("courses.csv", b"course_id,tag_list,region,region_copy\nC1,sql;python,North,North\nC2,sql;bi,North,North\n")[0]
    tables = build_initial_schema([dataset])
    findings = analyse_normalization_opportunities([dataset], tables)
    categories = {finding["category"] for finding in findings}
    assert "Possible repeating values" in categories
    assert "Potential duplicate attributes" in categories
    # This module returns suggestions only, it does not mutate tables or source values.
    assert len(tables) == 1
    assert dataset.frame.loc[0, "tag_list"] == "sql;python"
