from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    pass


class InsufficientBalanceError(LLMError):
    """Terminal error: callers must stop and must not retry."""


def _is_balance_error(status: int | None, message: str) -> bool:
    lowered = message.casefold()
    markers = ("insufficient balance", "insufficient_balance", "余额不足", "recharge")
    return status == 402 or any(marker in lowered for marker in markers)


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    usage: dict
    request_id: str | None


class DeepSeekClient:
    def __init__(self, model="deepseek-v4-flash", base_url="https://api.deepseek.com",
                 api_key_env="DEEPSEEK_API_KEY", timeout=60.0) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> ChatResult:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise LLMError(f"Missing required environment variable: {self.api_key_env}")
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
            raise LLMError(f"DeepSeek HTTP {error.code}: {detail[:500]}") from error
        except urllib.error.URLError as error:
            raise LLMError(f"DeepSeek connection failed: {error.reason}") from error
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError("DeepSeek response does not contain assistant content") from error
        return ChatResult(str(text).strip(), str(payload.get("model", self.model)),
                          payload.get("usage") or {}, request_id)
