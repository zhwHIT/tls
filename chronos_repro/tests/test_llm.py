import json
import urllib.error

import pytest

from chronos_repro.llm import ChatResult, DeepSeekClient, InsufficientBalanceError, LLMError
from chronos_repro.strategies import chronos_query, direct_query, rewrite_query


class FakeClient:
    def chat(self, messages, temperature=0.0):
        assert temperature == 0.0
        return ChatResult(
            "query: Egypt Mubarak resignation February 2011",
            "deepseek-v4-flash",
            {"total_tokens": 12},
            "test",
        )


def test_three_query_strategies():
    assert direct_query(" Egypt crisis ") == "Egypt crisis"
    rewritten, audit = rewrite_query(FakeClient(), "Egypt crisis")
    assert rewritten == "Egypt Mubarak resignation February 2011"
    query, _ = chronos_query(FakeClient(), "Egypt crisis", 2, ["Egypt crisis"], [])
    assert query == rewritten
    assert audit["usage"]["total_tokens"] == 12


def test_missing_key_stops_before_network(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        DeepSeekClient().chat([{"role": "user", "content": "test"}])


def test_balance_error_stops_without_retry(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    error = urllib.error.HTTPError("url", 402, "Payment Required", {}, None)
    error.read = lambda: json.dumps({"error": {"message": "Insufficient Balance"}}).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(error))
    with pytest.raises(InsufficientBalanceError, match="without retry"):
        DeepSeekClient().chat([{"role": "user", "content": "test"}])
