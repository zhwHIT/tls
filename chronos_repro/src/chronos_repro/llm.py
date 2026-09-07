from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    pass


class RetryableLLMError(LLMError):
    pass


class NonRetryableLLMError(LLMError):
    pass


class InsufficientBalanceError(LLMError):
    """Terminal error: callers must stop and must not retry."""


def _is_balance_error(status: int | None, message: str) -> bool:
    lowered = message.casefold()
    markers = ("insufficient balance", "insufficient_balance", "余额不足", "recharge")
    return status == 402 or any(marker in lowered for marker in markers)


def _is_retryable_http(status: int) -> bool:
    return status in {408, 409, 425, 429} or 500 <= status <= 599


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    usage: dict
    request_id: str | None
    attempts: int = 1


class DeepSeekClient:
    def __init__(self, model="deepseek-v4-flash", base_url="https://api.deepseek.com",
                 api_key_env="DEEPSEEK_API_KEY", timeout=60.0, max_retries=2,
                 retry_backoff_seconds=1.0) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def _wait_before_retry(self, attempt: int) -> None:
        delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
        if delay:
            time.sleep(delay)

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> ChatResult:
        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                result = self._chat_once(messages, temperature)
                return ChatResult(
                    result.text, result.model, result.usage, result.request_id, attempt
                )
            except RetryableLLMError as error:
                if attempt == total_attempts:
                    raise LLMError(f"{error} after {attempt} attempts") from error
                self._wait_before_retry(attempt)
        raise AssertionError("unreachable")

    def _chat_once(self, messages: list[dict[str, str]], temperature: float = 0.0) -> ChatResult:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise NonRetryableLLMError(
                f"Missing required environment variable: {self.api_key_env}"
            )
        body = json.dumps({"model": self.model, "messages": messages,
                           "temperature": temperature}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                request_id = response.headers.get("x-request-id")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if _is_balance_error(error.code, detail):
                raise InsufficientBalanceError(
                    "DeepSeek balance is insufficient; execution stopped without retry") from error
            error_type = RetryableLLMError if _is_retryable_http(error.code) else NonRetryableLLMError
            qualifier = "retryable" if _is_retryable_http(error.code) else "not retryable after analysis"
            raise error_type(
                f"DeepSeek HTTP {error.code} ({qualifier}): {detail[:500]}"
            ) from error
        except urllib.error.URLError as error:
            raise RetryableLLMError(f"DeepSeek connection failed: {error.reason}") from error
        except (TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RetryableLLMError(f"DeepSeek response transport failed: {error}") from error
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RetryableLLMError(
                "DeepSeek response does not contain assistant content"
            ) from error
        text = str(text).strip()
        if not text:
            raise RetryableLLMError("DeepSeek returned empty assistant content")
        return ChatResult(text, str(payload.get("model", self.model)),
                          payload.get("usage") or {}, request_id)
