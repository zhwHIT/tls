from datetime import date

import pytest

from chronos_repro.gold_supervision import (
    align_reference_events,
    build_masked_state,
    query_copy_ratio,
    select_targets,
    validate_cross_dataset_topic_splits,
    validate_teacher_query,
)


def test_aligns_similar_nearby_events_across_references():
    timelines = (
        {date(2020, 1, 1): ("Leader won the national election",)},
        {date(2020, 1, 2): ("Leader wins national election",)},
    )
    events = align_reference_events("topic", timelines)
    assert len(events) == 1
    assert events[0]["reference_count"] == 2
    assert events[0]["accepted_dates"] == ["2020-01-01", "2020-01-02"]


def test_masked_date_does_not_expose_private_date():
    events = [
        {"event_id": "a", "canonical_date": "2020-01-01", "summary": "before", "consensus": 1.0},
        {"event_id": "b", "canonical_date": "2020-02-01", "summary": "target event", "consensus": 1.0},
        {"event_id": "c", "canonical_date": "2020-03-01", "summary": "after", "consensus": 1.0},
    ]
    state = build_masked_state("topic", events, events[1], "MISSING_DATE", 2)
    masked = next(row for row in state["events"] if row["event_id"] == "target_masked")
    assert masked["time"]["value"] is None
    assert state["valid_actions"] == ["SEARCH"]


def test_target_selection_prefers_multi_reference_consensus():
    events = [
        {"event_id": "a", "canonical_date": "2020-01-01", "summary": "long unique event text", "reference_count": 1},
        {"event_id": "b", "canonical_date": "2020-02-01", "summary": "consensus event", "reference_count": 2},
    ]
    assert select_targets(events, 1)[0]["event_id"] == "b"


def test_query_copy_guard_rejects_gold_summary_copy():
    summary = "Leader won the national election after a close campaign"
    assert query_copy_ratio("leader national election 2020", summary) < 1
    with pytest.raises(ValueError, match="copies too much"):
        validate_teacher_query(summary, "topic timeline", summary, 0.8)


def test_cross_dataset_split_validation_accepts_aligned_groups():
    assignments = {
        "crisis": {"syria": "test"},
        "t17": {"syria": "test"},
        "entities": {"Bashar_al-Assad": "test"},
    }
    checked = validate_cross_dataset_topic_splits(
        assignments,
        [["crisis:syria", "t17:syria", "entities:Bashar_al-Assad"]],
    )
    assert len(checked) == 1


def test_cross_dataset_split_validation_rejects_semantic_leakage():
    assignments = {
        "crisis": {"syria": "dev"},
        "t17": {"syria": "test"},
    }
    with pytest.raises(ValueError, match="topic leakage"):
        validate_cross_dataset_topic_splits(assignments, [])
