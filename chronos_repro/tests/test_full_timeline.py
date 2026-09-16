from datetime import date

import pytest

from chronos_repro.full_timeline import (
    apply_merge_operations,
    evaluate_gold_coverage,
    prediction_from_timeline,
    validate_merge_operations,
    validate_resolved_update,
    validate_verified_candidates,
)


def test_batch_verify_and_merge_multiple_events():
    docs = [{"id": "a"}, {"id": "b"}]
    raw = [
        {"candidate_id": "c1", "status": "SUPPORTED", "event": {"time": "2020-01-01", "summary": "Person began an important role"}, "evidence_ids": ["a"], "confidence": 0.8},
        {"candidate_id": "c2", "status": "SUPPORTED", "event": {"time": "2021-01-01", "summary": "Person ended the important role"}, "evidence_ids": ["b"], "confidence": 0.9},
    ]
    for row, doc in zip(raw, docs):
        doc["text"] = row["event"]["time"] + ": " + row["event"]["summary"]
        row["date_evidence"] = {"document_id": doc["id"], "quote": doc["text"], "time_expression": row["event"]["time"]}
    candidates = validate_verified_candidates(raw, docs, 3)
    operations = validate_merge_operations(
        [{"candidate_id": "c1", "operation": "APPEND"}, {"candidate_id": "c2", "operation": "APPEND"}],
        candidates,
        [],
    )
    timeline, audit = apply_merge_operations([], candidates, operations)
    assert len(timeline) == len(audit) == 2
    assert len(prediction_from_timeline(timeline)) == 2


def test_verify_rejects_unretrieved_evidence():
    raw = [{"candidate_id": "c1", "status": "SUPPORTED", "event": {"time": "2020-01-01", "summary": "Person began an important role"}, "evidence_ids": ["missing"], "confidence": 0.8}]
    with pytest.raises(ValueError, match="unavailable"):
        validate_verified_candidates(raw, [{"id": "a"}], 3)


def test_gold_coverage_uses_date_and_semantics():
    references = ({date(2020, 1, 1): ("Person began an important role",)},)
    prediction = [["2020-01-01", ["Person begins important role"]]]
    result = evaluate_gold_coverage("person", prediction, references, 0.2)
    assert result["gold_date_recall"] == 1.0
    assert result["gold_event_recall"] == 1.0


def test_update_requires_validated_model_fusion():
    existing = {"event_id": "e1", "time": "2020-01-01", "summary": "Person began an important role", "actors": [], "location": None, "evidence_ids": ["a"], "confidence": 0.8, "conflict": False}
    candidate = {"candidate_id": "c1", "status": "SUPPORTED", "event": {"time": "2020-01-01", "summary": "Person began the important national role", "actors": ["Person"], "location": None}, "evidence_ids": ["b"], "confidence": 0.9}
    operation = {"candidate_id": "c1", "operation": "UPDATE", "target_event_id": "e1", "reason": "same event"}
    merged = validate_resolved_update(existing, candidate, {"event_id": "e1", "time": "2020-01-01", "summary": "Person began the important national role", "actors": ["Person"], "location": None, "evidence_ids": ["a", "b"], "confidence": 0.9, "conflict": False})
    timeline, _ = apply_merge_operations([existing], [candidate], [operation], {"c1": merged})
    assert timeline[0]["evidence_ids"] == ["a", "b"]
