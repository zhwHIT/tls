from __future__ import annotations

import json
from datetime import date

from .exploration import query_similarity


SYSTEM_PROMPT = (
    "You are a closed-domain timeline search planner. Given the current retrieval "
    "state, return exactly one concise English search query that finds important "
    "missing event dates. Return only the query."
)


def result_dates(results: list[dict]) -> set[date]:
    return {
        date.fromisoformat(item["timestamp"][:10])
        for item in results
        if item.get("timestamp") and len(item["timestamp"]) >= 10
    }


def covered_gold(found: set[date], gold: set[date], window_days: int = 0) -> set[date]:
    return {
        target
        for target in gold
        if any(abs((candidate - target).days) <= window_days for candidate in found)
    }


def score_candidate(
    query: str,
    results: list[dict],
    covered_dates: set[date],
    seen_ids: set[str],
    gold_dates: set[date],
    previous_queries: list[str],
) -> dict:
    new_results = [item for item in results if str(item["id"]) not in seen_ids]
    found_dates = result_dates(new_results)
    updated_dates = covered_dates | found_dates
    exact_before = covered_gold(covered_dates, gold_dates)
    exact_after = covered_gold(updated_dates, gold_dates)
    window_before = covered_gold(covered_dates, gold_dates, 2)
    window_after = covered_gold(updated_dates, gold_dates, 2)
    similarity = max(
        (query_similarity(query, previous) for previous in previous_queries),
        default=0.0,
    )
    metrics = {
        "new_document_count": len(new_results),
        "new_date_count": len(found_dates - covered_dates),
        "exact_gold_gain": len(exact_after - exact_before),
        "window_2d_gold_gain": len(window_after - window_before),
        "max_previous_query_similarity": similarity,
        "query_novelty": 1.0 - similarity,
    }
    metrics["reward"] = (
        1000.0 * metrics["exact_gold_gain"]
        + 100.0 * metrics["window_2d_gold_gain"]
        + 2.0 * metrics["new_date_count"]
        + metrics["query_novelty"]
    )
    return metrics


def rank_candidates(candidates: list[dict]) -> list[dict]:
    return sorted(
        candidates,
        key=lambda item: (
            -item["metrics"]["reward"],
            item["query"].casefold(),
        ),
    )


def prompt_messages(state: dict) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(state, ensure_ascii=False, sort_keys=True),
        },
    ]
