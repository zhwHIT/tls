import copy
from pathlib import Path

from chronos_repro.exploration_memory import initial_memory, apply_memory_output
from chronos_repro.tisa_rollout import SKELETON, REFINE


def fixture_trace():
    before = initial_memory([])
    output = {"thought": "No evidence was returned for this interval.", "action": "MEMORY_UPDATE",
              "observations": [], "discovered_keywords": [], "stage_outline": [],
              "next_search_directions": ["Try a different query."], "skeleton_ready": False,
              "boundary_assessment": "The boundaries are not established."}
    after = apply_memory_output(before, output, "example later developments", "LATER", [])
    return {"final_exploration_memory": after, "steps": [
        {"step_id": "s1", "phase": SKELETON, "action": "SEARCH",
         "model_output": {"query": "example later developments", "strategy": "LATER"}},
        {"step_id": "s2", "phase": SKELETON, "action": "MEMORY_UPDATE", "model_output": output,
         "model_input": {"memory": before, "tool_observation": {"retrieved_documents": []}},
         "observation": {"memory_after": copy.deepcopy(after)}},
        {"step_id": "s3", "phase": REFINE, "action": "GAP_MEMORY",
         "model_input": {"memory": {"exploration": copy.deepcopy(after)}}},
    ]}


def test_memory_audit_replays_transition_and_checks_inheritance(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from analyze_exploration_rollout import audit_memory
    result = audit_memory(fixture_trace())
    assert result["memory_transitions_valid"]
    assert result["memory_transitions_checked"] == 1
    assert result["phase2_inherits_memory"]


def test_memory_audit_rejects_changed_history_and_missing_inheritance(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from analyze_exploration_rollout import audit_memory
    trace = fixture_trace()
    trace["steps"][1]["observation"]["memory_after"]["search_history"][0]["query"] = "tampered"
    trace["steps"][2]["model_input"]["memory"] = {}
    result = audit_memory(trace)
    assert not result["memory_transitions_valid"]
    assert not result["phase2_inherits_memory"]
    assert result["failures"][0]["step_id"] == "s2"
