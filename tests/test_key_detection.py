import pandas as pd

from modules.key_detection import best_primary_key, detect_candidate_keys


def test_valid_primary_key_detection():
    frame = pd.DataFrame({"student_id": ["S001", "S002", "S003"], "name": ["A", "B", "C"]})
    key = best_primary_key(frame)
    assert key is not None
    assert key["columns"] == ["student_id"]
    assert key["valid_primary_key"] is True


def test_duplicate_key_not_marked_valid():
    frame = pd.DataFrame({"id": [1, 2, 2], "name": ["A", "B", "C"]})
    candidates = detect_candidate_keys(frame)
    id_candidate = next(candidate for candidate in candidates if candidate["columns"] == ["id"])
    assert id_candidate["valid_primary_key"] is False
    assert id_candidate["duplicate_count"] == 1


def test_null_key_not_marked_valid():
    frame = pd.DataFrame({"id": ["S1", None, "S3"]})
    candidate = next(candidate for candidate in detect_candidate_keys(frame) if candidate["columns"] == ["id"])
    assert candidate["valid_primary_key"] is False
    assert candidate["missing_count"] == 1


def test_composite_key_candidate():
    frame = pd.DataFrame({"student_id": ["S1", "S1", "S2"], "course_id": ["C1", "C2", "C1"]})
    candidates = detect_candidate_keys(frame)
    assert any(candidate["kind"] == "composite" and candidate["valid_primary_key"] for candidate in candidates)
