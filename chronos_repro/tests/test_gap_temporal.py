import copy
import gzip
import json
from pathlib import Path

import pytest

from chronos_repro.gap_temporal import validate_temporal_action, visible_temporal_state
from chronos_repro.llm import ChatResult
from chronos_repro.retrieval import build_bm25_index
from chronos_repro.tisa_rollout import REFINE, student_state


EVENTS = [
    {"event_id": "e1", "time": "2020-01-01", "summary": "Protests begin"},
    {"event_id": "e2", "time": "2020-02-01", "summary": "Government resigns"},
]
GAP = {"gap_id": "g1", "type": "CAUSAL_GAP", "description": "The transition needs supporting evidence.",
       "left_event_id": "e1", "right_event_id": "e2", "priority": 0.9, "status": "OPEN"}
GAP['retrieval_target'] = {
    'question': 'What reported incident directly triggered the government resignation?',
    'anchor_quote': 'Government resigns',
    'completion_criterion': 'A report names the direct trigger cited for the resignation.',
    'seed_query': 'government resignation reported direct trigger',
}
CONFIG = {"temporal_search": {"enabled": True, "soft_penalty": 0.5, "max_padding_days": 90},
          "phase2_teacher_guidance": False, "label_repair_attempts": 1, "temperature": 0,
          "phase2_max_gaps": 4, "phase2_max_gap_cycles": 2, "top_k": 8, "document_char_limit": 700}
FILTER = {"mode": "hard", "date_from": "2020-01-01", "date_to": "2020-02-01",
          "anchor_event_ids": ["e1", "e2"], "padding_days": 0}


def visible():
    return visible_temporal_state(student_state("crisis", "egypt", REFINE, EVENTS, {"gaps": [GAP]}, ["SEARCH"], GAP), CONFIG)


def action(date_filter=None):
    return {"thought": "Check the transition between these dated events.", "action": "SEARCH",
            "gap_id": "g1", "query": "Egypt transition evidence", "date_filter": copy.deepcopy(date_filter or FILTER)}


@pytest.mark.parametrize("change", [
    {"anchor_event_ids": ["private-event"]}, {"date_from": "2019-12-31"},
    {"padding_days": -1}, {"padding_days": True}, {"padding_days": 91},
    {"mode": "hard", "anchor_event_ids": ["e1"]},
    {"date_to": "2019-01-01"}, {"mode": "invalid"},
])
def test_invalid_or_private_date_window_is_rejected(change):
    with pytest.raises(ValueError):
        validate_temporal_action(action({**FILTER, **change}), visible(), CONFIG)


def test_single_visible_anchor_supports_soft_padding():
    filt = {"mode": "soft", "anchor_event_ids": ["e1"], "padding_days": 14,
            "date_from": "2019-12-18", "date_to": "2020-01-15"}
    assert validate_temporal_action(action(filt), visible(), CONFIG)["date_filter"] == filt
    assert validate_temporal_action({"query": "old query"}, {}, {}) == {"query": "old query"}


def test_empty_query_requires_none_and_no_anchors():
    a = action()
    a["query"] = ""
    with pytest.raises(ValueError, match="empty query"):
        validate_temporal_action(a, visible(), CONFIG)
    a["date_filter"] = {"mode": "none", "date_from": None, "date_to": None, "anchor_event_ids": [], "padding_days": 0}
    assert validate_temporal_action(a, visible(), CONFIG)["query"] == ""


def test_memory_context_recomputed_from_current_events():
    state = visible()
    state["events"][1]["time"] = "2020-03-01"
    refreshed = visible_temporal_state(state, CONFIG)
    assert refreshed["active_gap"]["temporal_context"]["date_to"] == "2020-03-01"
    assert state["active_gap"]["temporal_context"]["date_to"] == "2020-02-01"


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def chat(self, messages, temperature=0):
        self.requests.append(copy.deepcopy(messages))
        return ChatResult(json.dumps(self.payloads.pop(0)), "fake-local", {"total_tokens": 10}, "local-test")


def test_phase2_temporal_repair_retrieval_memory_and_sft(tmp_path, monkeypatch):
    # Use a real local BM25 index; only model responses are deterministic fixtures.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_tisa_two_phase_annotation as runner

    folder = tmp_path / "data" / "egypt"
    folder.mkdir(parents=True)
    with gzip.open(folder / "articles.preprocessed.jsonl.gz", "wt", encoding="utf-8") as handle:
        for doc_id, timestamp in [("inside", "2020-01-15"), ("outside", "2021-01-15")]:
            handle.write(json.dumps({"id": doc_id, "title": "Egypt transition evidence", "text": "Egypt transition evidence", "time": timestamp}) + "\n")
    index = tmp_path / "index.sqlite3"
    build_bm25_index(folder.parent, index)
    new_gap = {**GAP, "gap_id": "g2", 'type': 'MISSING_FACTOR',
               "description": "Identify the organization associated with the protests.",
               'retrieval_target': {
                   'question': 'Which organization announced the initial protests?',
                   'anchor_quote': 'Protests begin',
                   'completion_criterion': 'A report identifies the organization that announced the protests.',
                   'seed_query': 'initial protests announcing organization',
               }}
    invalid = action({**FILTER, "date_from": "1900-01-01"})
    empty = {**action(), "gap_id": "g2", "query": "", "date_filter": {
        "mode": "none", "date_from": None, "date_to": None, "anchor_event_ids": [], "padding_days": 0}}
    client = FakeClient([
        {"student_output": {"action": "GAP_MEMORY", "thought": "The event sequence needs a causal bridge.", "gaps": [GAP]}},
        {"student_output": invalid}, {"student_output": action()},
        {"student_output": {"action": "GAP_MEMORY", "thought": "Keep the unresolved bridge and examine another context gap.", "resolved_gap_ids": [], "gaps": [new_gap]}},
        {"student_output": empty},
    ])
    monkeypatch.setattr(runner, "verify_batch", lambda *args: ({"candidates": []}, []))
    state = {"dataset": "crisis", "topic": "egypt", "timeline_events": copy.deepcopy(EVENTS), "search_history": []}
    trace = {"trajectory_id": "local-test", "dataset": "crisis", "topic": "egypt", "steps": [], "audits": []}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "logical_calls": 0, "http_attempts": 0}
    # An untried gap must get a turn even with lower priority than a retried gap.
    new_gap["priority"] = 0.1
    result = runner.run_phase2(client, state, [{"event_id": "PRIVATE_SENTINEL", "canonical_date": "1900-01-01"}], CONFIG, index, trace, usage)
    assert not client.payloads
    assert "PRIVATE_SENTINEL" not in json.dumps(client.requests)
    assert "teacher_only_target_events" not in json.dumps(client.requests)
    assert "teacher_only_reference_events" not in json.dumps(client.requests)
    assert result["teacher_alignment"] == {"g2": []}
    searches = [s for s in trace["steps"] if s["action"] == "SEARCH"]
    assert [r["id"] for r in searches[0]["observation"]["results"]] == ["inside"]
    assert searches[0]["observation"]["retrieval_parameters"]["date_filter_mode"] == "hard"
    assert "search_history" not in searches[0]["model_input"]["memory"]  # no future action leakage
    assert searches[1]["model_input"]["memory"]["search_history"][0]["result_ids"] == ["inside"]
    assert searches[1]["observation"]["skipped"]
    assert result["memory"]["search_history"][1]["skipped"]
    sft = runner.compile_sft_rows(trace)
    row = next(r for r in sft if r["metadata"]["action"] == "SEARCH")
    assert row["schema_version"] == 5
    assert json.loads(row["messages"][2]["content"])["date_filter"] == FILTER
    assert usage["total_tokens"] == 50  # includes invalid response, excludes unnecessary forced-STOP API call
    assert trace["steps"][-1]["observation"]["forced"] is True
    assert trace["steps"][-1]["observation"]["open_gap_count"] == 1
    assert not any(r["metadata"]["action"] == "STOP" for r in sft)
    assert all("budget" not in json.loads(r["messages"][1]["content"]) for r in sft)
