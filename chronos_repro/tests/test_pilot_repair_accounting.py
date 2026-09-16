import json
from pathlib import Path

from chronos_repro.llm import ChatResult


def test_verify_counts_returned_response_even_when_thought_needs_repair(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_full_timeline_api_agent as runner

    class Client:
        calls = 0
        def chat(self, messages, temperature=0):
            self.calls += 1
            assert "6-240 characters" in messages[0]["content"]
            thought = "a" * 300 if self.calls == 1 else "No supported event was found."
            return ChatResult(json.dumps({"thought": thought, "candidates": []}), "fake", {"total_tokens": 10}, "test")

    client = Client()
    state = {"topic": "egypt", "timeline_events": [], "search_history": [], "gaps": [], "valid_actions": ["VERIFY"]}
    result, audits = runner.verify_batch(client, state, [], {"max_candidates_per_round": 2, "label_repair_attempts": 1, "temperature": 0})
    assert result["candidates"] == []
    assert sum(row.get("usage", {}).get("total_tokens", 0) for row in audits) == 20
    assert sum("validation_error" in row for row in audits) == 1
    assert client.calls == 2
