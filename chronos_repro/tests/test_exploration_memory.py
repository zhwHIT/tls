import copy
import json
from pathlib import Path

import pytest

from chronos_repro.exploration_memory import (
    apply_memory_output, initial_memory, load_keywords, validate_exploration_policy,
    validate_memory_output, skeleton_stop_allowed, filter_invalid_memory_observations,
)
from chronos_repro.llm import ChatResult, InsufficientBalanceError
from chronos_repro.tisa_rollout import validate_student_state


DOC = {"id": "d1", "title": "Aurora launch", "text": "Aurora launched on 2020-04-03.",
       "publication_date": "2020-04-05"}


def output(ready=False):
    return {"thought": "The report provides a dated launch milestone.", "action": "MEMORY_UPDATE",
            "observations": [{"summary": "Aurora launched.", "event_time": "2020-04-03",
                              "time_expression": "2020-04-03", "document_id": "d1",
                              "evidence_quote": "Aurora launched on 2020-04-03."}],
            "discovered_keywords": ["Aurora"],
            "stage_outline": [{"period": "2020", "description": "Launch stage", "evidence_ids": ["d1"]}],
            "next_search_directions": [] if ready else ["Probe later developments."],
            "boundary_assessment": "Earlier development and later outcome need exploration.",
            "skeleton_ready": ready}


def test_memory_is_grounded_dated_and_not_promoted_to_verified_facts():
    memory = initial_memory(["Aurora"])
    checked = validate_memory_output(output(), memory, [DOC])
    updated = apply_memory_output(memory, checked, "Aurora probe launch", "DISCOVER", [DOC])
    assert memory["observed_events"] == []
    observation = updated["observed_events"][0]
    assert observation["event_time"] == "2020-04-03"
    assert observation["publication_date"] == "2020-04-05"
    assert observation["verification_status"] == "PROVISIONAL"
    assert updated["observed_date_range"] == {"from": "2020-04-03", "to": "2020-04-03"}
    again = apply_memory_output(updated, checked, "Aurora launch report", "FACET", [DOC])
    assert len(again["observed_events"]) == 1
    assert again["search_history"][-1]["new_document_count"] == 0
    assert again["search_history"][-1]["new_observation_count"] == 0


@pytest.mark.parametrize("field,value", [("document_id", "unknown"), ("evidence_quote", "Invented quotation."),
                                        ("event_time", "2020-02-30"), ("time_expression", "April 9")])
def test_memory_rejects_invalid_evidence_or_dates(field, value):
    payload = output()
    payload["observations"][0][field] = value
    with pytest.raises(ValueError):
        validate_memory_output(payload, initial_memory([]), [DOC])


def test_unknown_and_partial_event_dates_are_not_publication_dates():
    payload = output()
    payload["observations"][0].update(event_time=None, time_expression=None)
    checked = validate_memory_output(payload, initial_memory([]), [DOC])
    assert checked["observations"][0]["event_time"] is None
    payload["observations"][0].update(event_time=None, time_expression="2020")
    checked = validate_memory_output(payload, initial_memory([]), [DOC])
    assert checked["observations"][0]["time_expression"] == "2020"
    assert checked["observations"][0]["event_time"] is None
    payload["observations"][0].update(event_time="2020", time_expression="2020")
    assert validate_memory_output(payload, initial_memory([]), [DOC])["observations"][0]["event_time"] == "2020"


def test_keywords_and_ready_flag_are_checked():
    payload = output()
    payload["discovered_keywords"] = ["secret evaluator keyword"]
    with pytest.raises(ValueError, match="keyword"):
        validate_memory_output(payload, initial_memory([]), [DOC])
    payload = output()
    payload["skeleton_ready"] = True
    with pytest.raises(ValueError, match="skeleton_ready"):
        validate_memory_output(payload, initial_memory([]), [DOC])


def test_policy_rejects_repeat_and_premature_stop():
    visible = {"valid_actions": ["SEARCH", "STOP"], "memory": initial_memory([])}
    visible["memory"]["search_history"] = [{"query": "Aurora probe launch"}]
    with pytest.raises(ValueError, match="repeats"):
        validate_exploration_policy({"thought": "Search another stage now.", "action": "SEARCH",
                                     "query": "AURORA  probe launch", "strategy": "FACET"}, visible)
    with pytest.raises(ValueError, match="STOP"):
        validate_exploration_policy({"thought": "The stages are now complete.", "action": "STOP",
                                     "query": "", "strategy": "COMPLETE"}, visible)


def test_keywords_loaded_without_task_specific_enrichment(tmp_path):
    assert load_keywords(tmp_path) == []
    (tmp_path / "keywords.json").write_text('["egypt", "egyptian"]', encoding="utf-8")
    assert load_keywords(tmp_path) == ["egypt", "egyptian"]


def test_outline_can_keep_retrieved_history_without_an_extracted_observation():
    memory = initial_memory([])
    memory["search_history"] = [{"query": "Aurora historical overview", "result_ids": ["old-doc"]}]
    payload = output()
    payload["stage_outline"].append({"period": "earlier", "description": "Previously retrieved background.",
                                    "evidence_ids": ["old-doc"]})
    checked = validate_memory_output(payload, memory, [DOC])
    assert checked["stage_outline"][-1]["evidence_ids"] == ["old-doc"]
    payload["stage_outline"][-1]["evidence_ids"] = ["never-retrieved"]
    with pytest.raises(ValueError, match="stage must cite"):
        validate_memory_output(payload, memory, [DOC])


def test_partial_memory_filter_rejects_unsupported_dates_without_inventing_them():
    payload = output()
    bad = copy.deepcopy(payload["observations"][0])
    bad["time_expression"] = None
    payload["observations"].append(bad)
    checked, rejected = filter_invalid_memory_observations(payload, initial_memory([]), [DOC])
    assert len(checked["observations"]) == 1
    assert rejected[0]["observation_index"] == 1
    assert payload["observations"][1]["event_time"] == "2020-04-03"
    assert checked["thought"] == payload["thought"]


def test_partial_memory_filter_does_not_hide_global_or_all_row_failures():
    payload = output()
    payload["thought"] = "x" * 241
    with pytest.raises(ValueError, match="thought"):
        filter_invalid_memory_observations(payload, initial_memory([]), [DOC])


def test_partial_memory_filter_rejects_keywords_not_in_current_documents():
    payload = output()
    payload["discovered_keywords"].append("invented organization")
    checked, rejected = filter_invalid_memory_observations(payload, initial_memory([]), [DOC])
    assert checked["discovered_keywords"] == ["Aurora"]
    assert rejected[0]["kind"] == "keyword"
    assert rejected[0]["raw_keyword"] == "invented organization"
    payload = output()
    payload["observations"][0]["document_id"] = "invented"
    with pytest.raises(ValueError, match="all memory observations"):
        filter_invalid_memory_observations(payload, initial_memory([]), [DOC])


def _runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_tisa_two_phase_annotation as runner
    import exploration_phase
    return runner, exploration_phase


def test_real_runner_order_memory_input_consistency_and_forced_stop_exclusion(monkeypatch):
    runner, exploration = _runner(monkeypatch)
    import run_full_timeline_api_agent as full
    monkeypatch.setattr(exploration, "search", lambda *a, **k: [{"id": "d1"}])
    monkeypatch.setattr(exploration, "fetch_documents", lambda *a, **k: [copy.deepcopy(DOC)])

    class Client:
        search_count = 0
        verify_memories = []
        def chat(self, messages, temperature=0):
            request = json.loads(messages[1]["content"])
            assert "task_description" not in json.dumps(request)
            stage = request["stage"]
            if "choose diversified" in stage:
                self.search_count += 1
                visible = request["student_visible_state"]
                validate_student_state(visible)
                assert "few_shot_examples" in request
                if self.search_count == 2:
                    assert len(visible["memory"]["observed_events"]) == 1
                answer = {"thought": "Probe another coarse mission stage.", "action": "SEARCH",
                          "query": f"Aurora mission stage {self.search_count}", "strategy": "LATER"}
            elif "MEMORY_UPDATE" in stage:
                answer = output()
            elif stage == "VERIFY":
                self.verify_memories.append(request["state_before_verify"]["memory"])
                answer = {"thought": "No additional verified candidate is available.", "candidates": []}
            else:
                raise AssertionError(stage)
            return ChatResult(json.dumps(answer), "fake", {}, "offline-test")

    state = {"dataset": "fixture", "topic": "Aurora", "keywords": ["Aurora"], "timeline_events": [],
             "search_history": [], "gaps": [], "valid_actions": ["SEARCH"],
             "exploration_memory": initial_memory(["Aurora"])}
    config = {"exploration_memory": {"enabled": True}, "phase1_max_rounds": 2,
              "phase1_min_search_rounds": 1, "phase1_min_events": 1, "top_k": 2,
              "document_char_limit": 700, "label_repair_attempts": 1, "temperature": 0,
              "max_candidates_per_round": 2}
    trace = {"trajectory_id": "fixture", "dataset": "fixture", "topic": "Aurora", "steps": [], "audits": []}
    usage = dict.fromkeys(["prompt_tokens", "completion_tokens", "total_tokens", "logical_calls", "http_attempts"], 0)
    client = Client()
    runner.run_phase1(client, state, config, "unused", trace, usage)
    assert [s["action"] for s in trace["steps"]] == ["SEARCH", "MEMORY_UPDATE", "VERIFY", "MERGE"] * 2 + ["STOP"]
    assert state["phase1_termination"] == "runner_limit"
    assert len(state["exploration_memory"]["search_history"]) == 2
    assert client.verify_memories[0]["observed_events"]
    for step in trace["steps"]:
        validate_student_state(step["model_input"])
    sft = runner.compile_sft_rows(trace)
    assert not any(r["metadata"]["action"] == "STOP" for r in sft)
    assert any(r["metadata"]["action"] == "MEMORY_UPDATE" for r in sft)
    assert full.compact_state(state) == state["model_visible_state"]
    from analyze_exploration_rollout import audit_memory
    assert audit_memory(trace)["post_merge_progress_checked"] == 2
    assert audit_memory(trace)["memory_transitions_valid"]
    assert state["exploration_memory"]["search_history"][0]["new_verified_event_count"] == 0


def test_phase2_initialization_inherits_exploration_without_gold(monkeypatch):
    runner, _ = _runner(monkeypatch)
    memory = initial_memory(["Aurora"])
    memory["search_history"] = [{"query": "Aurora probe launch", "result_ids": ["d1"]}]
    state = {"dataset": "fixture", "topic": "Aurora", "timeline_events": [], "exploration_memory": memory}

    class Client:
        def chat(self, messages, temperature=0):
            request = json.loads(messages[1]["content"])
            assert "teacher_only_reference_events" not in request
            assert request["student_visible_state"]["memory"]["exploration"] == memory
            return ChatResult(json.dumps({"student_output": {"thought": "No verified events yet support a bounded gap.",
                                                             "action": "GAP_MEMORY", "gaps": []}}), "fake", {}, "test")

    runner.initialize_gap_memory(Client(), state, [{"event_id": "private"}],
                                 {"phase2_teacher_guidance": False, "phase2_max_gaps": 8,
                                  "label_repair_attempts": 0, "temperature": 0})


def test_balance_failure_is_not_retried(monkeypatch):
    runner, _ = _runner(monkeypatch)
    class Client:
        calls = 0
        def chat(self, *args, **kwargs):
            self.calls += 1
            raise InsufficientBalanceError("insufficient balance")
    client = Client()
    with pytest.raises(InsufficientBalanceError):
        runner.repaired_call(client, "policy", {}, lambda x: x, {"label_repair_attempts": 4, "temperature": 0})
    assert client.calls == 1


def test_ready_memory_cannot_stop_before_boundary_probes():
    memory = initial_memory([])
    memory.update(skeleton_ready=True, stage_outline=[{}, {}, {}])
    config = {"phase1_min_search_rounds": 6, "phase1_min_events": 4}
    events = [{}, {}, {}, {}]
    assert not skeleton_stop_allowed(memory, events, 6, config)
    memory["search_history"] = [{"strategy": "EARLIER"}, {"strategy": "LATER"}]
    assert skeleton_stop_allowed(memory, events, 6, config)
    assert not skeleton_stop_allowed(memory, events, 5, config)
    memory["next_search_directions"] = ["Still need an intermediate milestone."]
    assert not skeleton_stop_allowed(memory, events, 6, config)


def test_new_memory_state_does_not_require_legacy_history(monkeypatch):
    runner, _ = _runner(monkeypatch)
    state = {"dataset": "fixture", "topic": "Aurora", "timeline_events": [],
             "exploration_memory": initial_memory([])}
    assert runner.phase1_visible(state, ["SEARCH"])["memory"] == state["exploration_memory"]


def test_autonomous_stop_is_retained_as_training_target(monkeypatch):
    runner, _ = _runner(monkeypatch)
    memory = initial_memory([])
    memory.update(skeleton_ready=True, stage_outline=[{}, {}, {}],
                  search_history=[{"strategy": "EARLIER"}, {"strategy": "LATER"}])
    state = {"dataset": "fixture", "topic": "Aurora", "timeline_events": [], "exploration_memory": memory}
    config = {"exploration_memory": {"enabled": True}, "phase1_min_search_rounds": 0,
              "phase1_min_events": 0, "phase1_max_rounds": 1, "temperature": 0, "label_repair_attempts": 0}
    trace = {"trajectory_id": "fixture", "dataset": "fixture", "topic": "Aurora", "steps": [], "audits": []}
    usage = dict.fromkeys(["prompt_tokens", "completion_tokens", "total_tokens", "logical_calls", "http_attempts"], 0)
    class Client:
        def chat(self, messages, temperature=0):
            assert "STOP" in json.loads(messages[1]["content"])["student_visible_state"]["valid_actions"]
            return ChatResult(json.dumps({"thought": "The coarse stages and boundary probes are complete.",
                                          "action": "STOP", "query": "", "strategy": "COMPLETE"}), "fake", {}, "test")
    runner.run_phase1(Client(), state, config, "unused", trace, usage)
    assert state["phase1_termination"] == "autonomous_stop"
    assert trace["steps"][0]["observation"]["forced"] is False
    assert runner.compile_sft_rows(trace)[0]["metadata"]["action"] == "STOP"
