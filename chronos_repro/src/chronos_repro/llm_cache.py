"""Replay successful responses for an interrupted, deterministic pilot run."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .llm import ChatResult


class CachedLLMClient:
    def __init__(self, client, directory: Path):
        self.client = client
        self.model = client.model
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.new_calls = 0
        self.last_response_usage = {}

    def chat(self, messages, temperature=0.0):
        identity = {
            "model": self.model,
            "base_url": self.client.base_url,
            "temperature": temperature,
            "messages": messages,
        }
        if getattr(self.client, 'request_options', None):
            identity['request_options'] = self.client.request_options
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        path = self.directory / (key + ".json")
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            if stored["request_sha256"] != key:
                raise ValueError("LLM cache request hash mismatch")
            result = stored["response"]
            self.last_response_usage = dict(result.get('usage', {}))
            self.hits += 1
            print(f"[llm] cache hit {self.hits}", flush=True)
            return ChatResult(result["text"], result["model"], {}, result.get("request_id"), attempts=0)
        print(f"[llm] requesting {self.new_calls + 1}", flush=True)
        result = self.client.chat(messages, temperature=temperature)
        self.last_response_usage = dict(result.usage)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"request_sha256": key, "response": asdict(result)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        from .atomic_io import replace_with_retry
        replace_with_retry(temporary, path)
        self.new_calls += 1
        print(f"[llm] received {self.new_calls}", flush=True)
        return result

    def statistics(self):
        usage = {}
        for path in self.directory.glob("*.json"):
            row = json.loads(path.read_text(encoding="utf-8"))
            for key, value in row["response"].get("usage", {}).items():
                if type(value) is int:
                    usage[key] = usage.get(key, 0) + value
        return {"cache_hits_this_run": self.hits, "new_calls_this_run": self.new_calls,
                "cache_entries": len(list(self.directory.glob("*.json"))),
                "all_cached_response_usage": usage}
