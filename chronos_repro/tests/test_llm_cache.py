import pytest

from chronos_repro.llm import ChatResult, InsufficientBalanceError
from chronos_repro.llm_cache import CachedLLMClient


class Client:
    model = "fake"
    base_url = "https://example.invalid"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, temperature=0):
        self.calls += 1
        return ChatResult("response", self.model, {"total_tokens": 10}, "id")


def test_cache_restarts_without_duplicate_charge_and_differentiates_prompts(tmp_path):
    source = Client()
    client = CachedLLMClient(source, tmp_path)
    messages = [{"role": "user", "content": "one"}]
    assert client.chat(messages).usage["total_tokens"] == 10
    restarted = CachedLLMClient(source, tmp_path)
    assert restarted.chat(messages).attempts == 0
    assert restarted.chat(messages).usage == {}
    restarted.chat([{"role": "user", "content": "two"}])
    assert source.calls == 2
    assert restarted.statistics()["all_cached_response_usage"]["total_tokens"] == 20


def test_balance_failure_is_not_cached_or_retried(tmp_path):
    source = Client()
    def fail(*args, **kwargs):
        source.calls += 1
        raise InsufficientBalanceError("stop")
    source.chat = fail
    with pytest.raises(InsufficientBalanceError):
        CachedLLMClient(source, tmp_path).chat([])
    assert source.calls == 1
    assert not list(tmp_path.glob("*.json"))
