import json
import ssl
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
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(InsufficientBalanceError, match="without retry"):
        DeepSeekClient().chat([{"role": "user", "content": "test"}])
    assert len(calls) == 1


class FakeResponse:
    headers = {"x-request-id": "retry-success"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": "recovered"}}],
            "usage": {"total_tokens": 7},
        }).encode()


def test_connection_error_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    calls = 0

    def urlopen(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError(ssl.SSLError("unexpected eof"))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = DeepSeekClient(max_retries=2, retry_backoff_seconds=0).chat(
        [{"role": "user", "content": "test"}]
    )
    assert calls == 2
    assert result.text == "recovered"
    assert result.attempts == 2


def test_non_retryable_client_error_stops_after_analysis(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    calls = 0

    def urlopen(*args, **kwargs):
        nonlocal calls
        calls += 1
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        error.read = lambda: b'{"error":{"message":"invalid token"}}'
        raise error

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(LLMError, match="not retryable after analysis"):
        DeepSeekClient(max_retries=2, retry_backoff_seconds=0).chat(
            [{"role": "user", "content": "test"}]
        )
    assert calls == 1


def test_retryable_server_error_stops_after_limit(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    calls = 0

    def urlopen(*args, **kwargs):
        nonlocal calls
        calls += 1
        error = urllib.error.HTTPError("url", 503, "Unavailable", {}, None)
        error.read = lambda: b'{"error":{"message":"temporary"}}'
        raise error

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(LLMError, match="after 3 attempts"):
        DeepSeekClient(max_retries=2, retry_backoff_seconds=0).chat(
            [{"role": "user", "content": "test"}]
        )
    assert calls == 3
