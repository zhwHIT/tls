from __future__ import annotations

import re
from dataclasses import asdict
from typing import Protocol


class ChatClient(Protocol):
    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0): ...


def direct_query(topic_query: str) -> str:
    query = topic_query.strip()
    if not query:
        raise ValueError("topic_query must not be empty")
    return query


def _clean_query(text: str) -> str:
    text = text.strip().strip("`\"'")
    text = re.sub(r"^(query|search query)\s*:\s*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def rewrite_query(client: ChatClient, topic_query: str) -> tuple[str, dict]:
    result = client.chat([
        {"role": "system", "content": "Rewrite a news timeline topic into one concise English search query. Return only the query; do not answer the topic."},
        {"role": "user", "content": topic_query}], temperature=0.0)
    query = _clean_query(result.text)
    if not query:
        raise ValueError("LLM returned an empty rewritten query")
    return query, asdict(result)


def chronos_query(client: ChatClient, topic_query: str, round_number: int,
                  previous_queries: list[str], evidence: list[dict]) -> tuple[str, dict]:
    compact = [{"date": x.get("timestamp"), "title": x.get("title"),
                "snippet": x.get("snippet")} for x in evidence[-20:]]
    result = client.chat([
        {"role": "system", "content": "You are a timeline-search planner. Produce exactly one concise English search query seeking an important missing date, cause, consequence, or event detail. Return only the query and never fabricate an event."},
        {"role": "user", "content": f"Topic: {topic_query}\nRound: {round_number}\nPrevious queries: {previous_queries}\nEvidence: {compact}"}], temperature=0.0)
    query = _clean_query(result.text)
    if not query:
        raise ValueError("LLM returned an empty CHRONOS query")
    return query, asdict(result)
