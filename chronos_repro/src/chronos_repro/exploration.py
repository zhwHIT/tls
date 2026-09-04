from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .strategies import ChatClient, _clean_query


TOKEN = re.compile(r"[a-z0-9]+")


def topic_date_bounds(index: str | Path, topic: str) -> tuple[date, date]:
    with sqlite3.connect(Path(index)) as connection:
        row = connection.execute(
            "SELECT min(timestamp), max(timestamp) FROM documents WHERE topic = ?", (topic,)
        ).fetchone()
    if not row or not row[0] or not row[1]:
        raise ValueError(f"No dated documents for topic {topic!r}")
    return date.fromisoformat(row[0][:10]), date.fromisoformat(row[1][:10])


def date_gaps(
    start: date, end: date, covered_dates: set[date], minimum_days: int = 14
) -> list[dict]:
    points = sorted({start, end, *(day for day in covered_dates if start <= day <= end)})
    gaps = []
    for left, right in zip(points, points[1:]):
        days = (right - left).days
        if days >= minimum_days:
            gaps.append({"start": left.isoformat(), "end": right.isoformat(), "days": days})
    return sorted(gaps, key=lambda item: (-item["days"], item["start"]))


def query_similarity(left: str, right: str) -> float:
    a, b = set(TOKEN.findall(left.casefold())), set(TOKEN.findall(right.casefold()))
    return len(a & b) / len(a | b) if a or b else 1.0


def exploration_query(
    client: ChatClient,
    topic_query: str,
    gaps: list[dict],
    previous_queries: list[str],
    evidence: list[dict],
    similarity_limit: float = 0.75,
) -> tuple[str, dict]:
    target = gaps[0] if gaps else None
    compact = [
        {"date": item.get("timestamp"), "title": item.get("title")}
        for item in evidence[-12:]
    ]
    result = client.chat(
        [
            {
                "role": "system",
                "content": (
                    "Generate exactly one English news search query for timeline coverage. "
                    "Target the specified uncovered date interval, seek a distinct event, "
                    "include relevant year(s), and return only the query. Do not answer."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Topic: {topic_query}\nTarget gap: {target}\nOther gaps: {gaps[1:4]}\n"
                    f"Previous queries to avoid: {previous_queries}\nRecent evidence: {compact}"
                ),
            },
        ],
        temperature=0.0,
    )
    query = _clean_query(result.text)
    too_similar = max((query_similarity(query, old) for old in previous_queries), default=0.0)
    fallback = False
    if not query or too_similar > similarity_limit:
        if target is None:
            raise ValueError("No exploration gap and LLM query is empty or repetitive")
        query = f"{topic_query} events {target['start']} to {target['end']}"
        fallback = True
    audit = asdict(result)
    audit.update({"target_gap": target, "max_previous_similarity": too_similar,
                  "deterministic_fallback": fallback})
    return query, audit
