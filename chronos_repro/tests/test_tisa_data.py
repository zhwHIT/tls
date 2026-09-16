import pytest

from chronos_repro.tisa_data import (
    parse_json_object,
    tool_call,
    validate_evidence_ids,
    validate_preference_scores,
)


def test_parse_teacher_json_accepts_code_fence():
    assert parse_json_object('```json\n{"query": "x"}\n```') == {"query": "x"}


def test_tool_call_serializes_arguments():
    message = tool_call("call-1", "SEARCH", {"query": "x"})
    assert message["tool_calls"][0]["function"]["name"] == "SEARCH"
    assert '"query": "x"' in message["tool_calls"][0]["function"]["arguments"]


def test_verify_rejects_hallucinated_document_id():
    with pytest.raises(ValueError, match="unavailable"):
        validate_evidence_ids(["missing"], [{"id": "real"}])


def test_all_local_preferences_require_margin():
    scores = {
        name.lower(): {"chosen": 0.9, "rejected": 0.5}
        for name in ("SEARCH", "VERIFY", "MERGE", "STOP")
    }
    validate_preference_scores(scores, 0.2)
    scores["stop"] = {"chosen": 0.6, "rejected": 0.5}
    with pytest.raises(ValueError, match="STOP"):
        validate_preference_scores(scores, 0.2)
