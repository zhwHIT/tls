import pytest

from chronos_repro.tisa_rollout import (
    REFINE,
    apply_gap_update,
    next_open_gap,
    student_state,
    validate_gap_memory_action,
    validate_gap_search_action,
    validate_student_state,
)


EVENTS = [
    {"event_id": "e2", "time": "2020-02-01", "summary": "second event"},
    {"event_id": "e1", "time": "2020-01-01", "summary": "first event"},
]


def test_student_state_removes_budget_and_sorts_events():
    state = student_state(
        "crisis", "egypt", REFINE, EVENTS, {"gaps": []}, ["GAP_MEMORY"]
    )
    assert [row["event_id"] for row in state["events"]] == ["e1", "e2"]
    assert "budget" not in state
    with pytest.raises(ValueError, match="budget"):
        validate_student_state({**state, "memory": {"queries_left": 2}})


def test_gap_memory_then_empty_search_moves_to_next_gap():
    init = validate_gap_memory_action(
        {
            "thought": "The transition between the two events lacks a causal bridge.",
            "action": "GAP_MEMORY",
            "gaps": [{
                "gap_id": "gap-001",
                "type": "CAUSAL_GAP",
                "description": "The consequence is shown but its direct trigger is missing.",
                "priority": 0.9,
                "status": "OPEN",
                "left_event_id": "e1",
                "right_event_id": "e2",
            }],
        },
        EVENTS,
        4,
    )
    decision = validate_gap_search_action(
        {
            "thought": "Existing events already explain this interval sufficiently.",
            "action": "SEARCH",
            "gap_id": "gap-001",
            "query": "",
        },
        init["gaps"][0],
    )
    memory, audit = apply_gap_update(
        {"gaps": init["gaps"], "history": []},
        "gap-001",
        decision["query"],
        [],
        [],
        EVENTS,
        4,
    )
    assert memory["gaps"][0]["status"] == "NO_SEARCH_NEEDED"
    assert audit["new_gap_ids"] == []
    assert next_open_gap(memory) is None


def test_merge_cycle_can_add_a_new_gap():
    memory = {"gaps": [{
        "gap_id": "gap-001",
        "type": "TEMPORAL_GAP",
        "description": "A major interval is unexplained.",
        "priority": 0.8,
        "status": "OPEN",
        "left_event_id": "e1",
        "right_event_id": "e2",
        "attempted_queries": [],
    }], "history": []}
    updated, audit = apply_gap_update(
        memory,
        "gap-001",
        "Egypt transition key event",
        ["gap-001"],
        [{
            "gap_id": "gap-002",
            "type": "MISSING_FACTOR",
            "description": "The newly added outcome lacks its institutional cause.",
            "priority": 0.6,
            "status": "OPEN",
            "left_event_id": "e1",
            "right_event_id": "e2",
        }],
        EVENTS,
        4,
    )
    assert audit == {
        "resolved_gap_ids": ["gap-001"],
        "new_gap_ids": ["gap-002"],
    }
    assert next_open_gap(updated)["gap_id"] == "gap-002"


def test_student_thought_rejects_supervision_leak():
    with pytest.raises(ValueError, match="teacher-only"):
        validate_gap_search_action(
            {
                "thought": "Gold shows a missing event here.",
                "action": "SEARCH",
                "gap_id": "g",
                "query": "",
            },
            {"gap_id": "g"},
        )
