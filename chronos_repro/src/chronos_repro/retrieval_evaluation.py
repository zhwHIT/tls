from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date


def stratified_events(events: list[dict], maximum: int) -> list[dict]:
    """Select deterministic events spread across the full timeline."""
    if maximum < 1:
        raise ValueError("maximum must be positive")
    ordered = sorted(events, key=lambda row: (row["canonical_date"], row["event_id"]))
    if len(ordered) <= maximum:
        return ordered
    if maximum == 1:
        return [ordered[len(ordered) // 2]]
    positions = [
        round(index * (len(ordered) - 1) / (maximum - 1))
        for index in range(maximum)
    ]
    return [ordered[position] for position in positions]


def parse_json_object(text: str) -> dict:
    """Parse a JSON object while tolerating a single Markdown code fence."""
    value = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL | re.I)
    if fenced:
        value = fenced.group(1)
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    return payload


def validate_judgments(payload: dict, expected_ids: list[str]) -> list[dict]:
    rows = payload.get("judgments")
    if not isinstance(rows, list):
        raise ValueError("Response must contain a judgments list")
    expected = list(map(str, expected_ids))
    by_id: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each judgment must be an object")
        document_id = str(row.get("id", ""))
        if document_id not in expected or document_id in by_id:
            raise ValueError(f"Unexpected or duplicate document id: {document_id!r}")
        grade = row.get("grade")
        if not isinstance(grade, int) or grade not in {0, 1, 2}:
            raise ValueError(f"Invalid grade for {document_id!r}")
        event_date = row.get("event_date")
        if event_date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(event_date)):
            raise ValueError(f"Invalid event_date for {document_id!r}")
        reason = str(row.get("reason", "")).strip()
        if not reason:
            raise ValueError(f"Missing reason for {document_id!r}")
        by_id[document_id] = {
            "id": document_id,
            "grade": grade,
            "event_date": event_date,
            "reason": reason,
        }
    missing = [document_id for document_id in expected if document_id not in by_id]
    if missing:
        raise ValueError(f"Missing judgments for document ids: {missing}")
    return [by_id[document_id] for document_id in expected]


def score_ranking(
    ranked_ids: list[str],
    grades: dict[str, int],
    cutoffs: list[int],
) -> dict:
    """Score one event ranking against pooled, completely judged candidates."""
    ranked = list(map(str, ranked_ids))
    relevant = {document_id for document_id, grade in grades.items() if grade >= 2}
    contextual = {document_id for document_id, grade in grades.items() if grade >= 1}
    output: dict[str, float | int | bool | None] = {
        "judged_relevant": len(relevant),
        "judged_contextual_or_relevant": len(contextual),
        "resolved": bool(relevant),
    }
    reciprocal_rank = 0.0
    for rank, document_id in enumerate(ranked, start=1):
        if document_id in relevant:
            reciprocal_rank = 1.0 / rank
            break
    output["mrr"] = reciprocal_rank
    for cutoff in cutoffs:
        prefix = ranked[:cutoff]
        found = relevant.intersection(prefix)
        output[f"success@{cutoff}"] = bool(found)
        output[f"precision@{cutoff}"] = len(found) / cutoff
        output[f"recall@{cutoff}"] = (
            len(found) / len(relevant) if relevant else None
        )
        dcg = sum(
            ((2 ** grades.get(document_id, 0)) - 1) / math.log2(rank + 1)
            for rank, document_id in enumerate(prefix, start=1)
        )
        ideal_grades = sorted(grades.values(), reverse=True)[:cutoff]
        idcg = sum(
            ((2 ** grade) - 1) / math.log2(rank + 1)
            for rank, grade in enumerate(ideal_grades, start=1)
        )
        output[f"ndcg@{cutoff}"] = dcg / idcg if idcg else None
    return output


def aggregate_scores(event_scores: list[dict], cutoffs: list[int]) -> dict:
    if not event_scores:
        raise ValueError("event_scores must not be empty")
    resolved = [row for row in event_scores if row["resolved"]]
    output: dict[str, float | int] = {
        "events": len(event_scores),
        "resolved_events": len(resolved),
        "unresolved_events": len(event_scores) - len(resolved),
        "mrr": sum(float(row["mrr"]) for row in event_scores) / len(event_scores),
    }
    for cutoff in cutoffs:
        output[f"success@{cutoff}"] = (
            sum(bool(row[f"success@{cutoff}"]) for row in event_scores)
            / len(event_scores)
        )
        output[f"precision@{cutoff}"] = (
            sum(float(row[f"precision@{cutoff}"]) for row in event_scores)
            / len(event_scores)
        )
        for metric in ("recall", "ndcg"):
            values = [
                float(row[f"{metric}@{cutoff}"])
                for row in resolved
                if row[f"{metric}@{cutoff}"] is not None
            ]
            output[f"{metric}@{cutoff}"] = (
                sum(values) / len(values) if values else 0.0
            )
    return output


def judgments_by_event(rows: list[dict]) -> dict[str, dict[str, int]]:
    output: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        output[str(row["event_id"])][str(row["document_id"])] = int(row["grade"])
    return dict(output)


def score_publication_date_proxy(
    results: list[dict],
    accepted_dates: list[str],
    cutoffs: list[int],
    window_days: int = 2,
) -> dict:
    """Diagnose whether ranked article publication dates reach an event date."""
    if window_days < 0:
        raise ValueError("window_days must be non-negative")
    targets = [date.fromisoformat(value) for value in accepted_dates]
    ranked_dates = [
        date.fromisoformat(str(row["timestamp"])[:10])
        if row.get("timestamp") and len(str(row["timestamp"])) >= 10
        else None
        for row in results
    ]
    exact = [found in targets if found else False for found in ranked_dates]
    window = [
        any(abs((found - target).days) <= window_days for target in targets)
        if found else False
        for found in ranked_dates
    ]
    output: dict[str, float | int | bool] = {
        "documents": len(results),
        "unique_documents": len({str(row["id"]) for row in results}),
        "unique_publication_dates": len({value for value in ranked_dates if value}),
    }
    for name, labels in (("exact", exact), (f"window_{window_days}d", window)):
        reciprocal_rank = next(
            (1.0 / rank for rank, relevant in enumerate(labels, start=1) if relevant),
            0.0,
        )
        output[f"{name}_mrr"] = reciprocal_rank
        for cutoff in cutoffs:
            output[f"{name}_hit@{cutoff}"] = any(labels[:cutoff])
    return output


def aggregate_publication_date_scores(
    rows: list[dict], cutoffs: list[int], window_days: int = 2
) -> dict:
    if not rows:
        raise ValueError("rows must not be empty")
    output: dict[str, float | int] = {
        "events": len(rows),
        "exact_mrr": sum(float(row["exact_mrr"]) for row in rows) / len(rows),
        f"window_{window_days}d_mrr": (
            sum(float(row[f"window_{window_days}d_mrr"]) for row in rows) / len(rows)
        ),
        "mean_unique_publication_dates": (
            sum(int(row["unique_publication_dates"]) for row in rows) / len(rows)
        ),
    }
    for name in ("exact", f"window_{window_days}d"):
        for cutoff in cutoffs:
            output[f"{name}_hit@{cutoff}"] = (
                sum(bool(row[f"{name}_hit@{cutoff}"]) for row in rows) / len(rows)
            )
    return output


def quota_fusion(
    bm25_ids: list[str], dense_ids: list[str], depth: int, dense_slots: int
) -> list[str]:
    """Keep a lexical prefix and fill a bounded semantic tail."""
    if not 0 <= dense_slots <= depth:
        raise ValueError("dense_slots must be between zero and depth")
    lexical_slots = depth - dense_slots
    output = list(dict.fromkeys(map(str, bm25_ids[:lexical_slots])))
    for document_id in map(str, dense_ids):
        if len(output) >= depth:
            break
        if document_id not in output:
            output.append(document_id)
    for document_id in map(str, bm25_ids[lexical_slots:]):
        if len(output) >= depth:
            break
        if document_id not in output:
            output.append(document_id)
    return output
