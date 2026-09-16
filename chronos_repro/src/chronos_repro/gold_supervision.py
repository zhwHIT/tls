from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta


TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "to", "was", "were", "with",
}
GAP_TYPES = ("MISSING_DATE", "CAUSAL_BRIDGE", "MISSING_ROLE", "EVIDENCE_CONFLICT")


def normalize_topic_id(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", topic.casefold())


def validate_cross_dataset_topic_splits(
    assignments: dict[str, dict[str, str]], groups: list[list[str]]
) -> dict[str, list[str]]:
    """Reject exact-name and configured semantic-topic leakage across splits."""
    by_normalized: dict[str, list[tuple[str, str]]] = {}
    for dataset, topic_splits in assignments.items():
        for topic, split_name in topic_splits.items():
            key = normalize_topic_id(topic)
            by_normalized.setdefault(key, []).append((f"{dataset}:{topic}", split_name))
    for key, members in by_normalized.items():
        member_splits = {split_name for _, split_name in members}
        if len(member_splits) > 1:
            raise ValueError(f"Normalized topic leakage for {key}: {sorted(members)}")

    configured: dict[str, str] = {
        f"{dataset}:{topic}": split_name
        for dataset, topic_splits in assignments.items()
        for topic, split_name in topic_splits.items()
    }
    checked_groups: dict[str, list[str]] = {}
    for group_index, group in enumerate(groups):
        unknown = sorted(set(group) - set(configured))
        if unknown:
            raise ValueError(f"Unknown cross-dataset topic references: {unknown}")
        group_splits = {configured[item] for item in group}
        if len(group_splits) != 1:
            raise ValueError(
                f"Cross-dataset topic leakage in group {group_index}: "
                f"{[(item, configured[item]) for item in group]}"
            )
        checked_groups[str(group_index)] = list(group)
    return checked_groups


def content_tokens(text: str) -> set[str]:
    return {token for token in TOKEN.findall(text.casefold()) if token not in STOPWORDS}


def text_similarity(left: str, right: str) -> float:
    a, b = content_tokens(left), content_tokens(right)
    return len(a & b) / len(a | b) if a or b else 1.0


def align_reference_events(
    topic_id: str,
    timelines: tuple[dict[date, tuple[str, ...]], ...],
    date_window_days: int = 2,
    similarity_threshold: float = 0.2,
) -> list[dict]:
    clusters: list[list[dict]] = []
    for reference_index, timeline in enumerate(timelines):
        for event_date, summaries in sorted(timeline.items()):
            for summary in summaries:
                item = {
                    "date": event_date,
                    "summary": summary.strip(),
                    "reference_index": reference_index,
                }
                candidates = []
                for index, cluster in enumerate(clusters):
                    if any(row["reference_index"] == reference_index for row in cluster):
                        continue
                    best_similarity = max(
                        text_similarity(summary, row["summary"]) for row in cluster
                    )
                    closest_days = min(
                        abs((event_date - row["date"]).days) for row in cluster
                    )
                    if closest_days <= date_window_days and best_similarity >= similarity_threshold:
                        candidates.append((best_similarity, -closest_days, -index, index))
                if candidates:
                    clusters[max(candidates)[-1]].append(item)
                else:
                    clusters.append([item])

    aligned = []
    for cluster in clusters:
        dates = sorted(row["date"] for row in cluster)
        canonical_date = dates[len(dates) // 2]
        representative = max(
            cluster,
            key=lambda row: (len(content_tokens(row["summary"])), row["summary"]),
        )
        references = sorted({row["reference_index"] for row in cluster})
        signature = "|".join(
            [topic_id, canonical_date.isoformat()]
            + sorted(row["summary"] for row in cluster)
        )
        aligned.append({
            "event_id": "gold-" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12],
            "canonical_date": canonical_date.isoformat(),
            "accepted_dates": sorted({row["date"].isoformat() for row in cluster}),
            "summary": representative["summary"],
            "summary_variants": sorted({row["summary"] for row in cluster}),
            "reference_indices": references,
            "reference_count": len(references),
            "reference_total": len(timelines),
            "consensus": len(references) / len(timelines),
        })
    return sorted(aligned, key=lambda row: (row["canonical_date"], row["event_id"]))


def select_targets(events: list[dict], maximum: int) -> list[dict]:
    if maximum < 1:
        raise ValueError("maximum must be positive")
    ranked = sorted(
        events,
        key=lambda row: (-row["reference_count"], -len(content_tokens(row["summary"])),
                         row["canonical_date"], row["event_id"]),
    )
    return sorted(ranked[:maximum], key=lambda row: (row["canonical_date"], row["event_id"]))


def build_masked_state(
    topic_id: str,
    events: list[dict],
    target: dict,
    gap_type: str,
    queries_left: int,
) -> dict:
    if gap_type not in GAP_TYPES:
        raise ValueError(f"Unsupported gap type: {gap_type}")
    target_index = next(index for index, row in enumerate(events) if row["event_id"] == target["event_id"])
    neighbor_indices = range(max(0, target_index - 2), min(len(events), target_index + 3))
    visible_events = []
    for index in neighbor_indices:
        row = events[index]
        if row["event_id"] == target["event_id"]:
            continue
        visible_events.append({
            "event_id": row["event_id"],
            "time": {"value": row["canonical_date"], "granularity": "day",
                     "confidence": row["consensus"]},
            "summary": row["summary"],
            "actors": [],
            "location": None,
            "evidence_ids": [],
            "support": 0,
            "conflict": False,
        })
    if gap_type in {"MISSING_DATE", "MISSING_ROLE", "EVIDENCE_CONFLICT"}:
        masked = {
            "event_id": "target_masked",
            "time": {
                "value": None if gap_type == "MISSING_DATE" else target["canonical_date"],
                "granularity": "day",
                "confidence": 0.0,
            },
            "summary": target["summary"],
            "actors": [],
            "location": None,
            "evidence_ids": [],
            "support": 0,
            "conflict": gap_type == "EVIDENCE_CONFLICT",
        }
        if gap_type == "EVIDENCE_CONFLICT":
            canonical = date.fromisoformat(target["canonical_date"])
            masked["time_candidates"] = [
                canonical.isoformat(),
                (canonical + timedelta(days=1)).isoformat(),
            ]
        visible_events.append(masked)
    target_date = date.fromisoformat(target["canonical_date"])
    previous_date = date.fromisoformat(events[max(0, target_index - 1)]["canonical_date"])
    next_date = date.fromisoformat(events[min(len(events) - 1, target_index + 1)]["canonical_date"])
    return {
        "topic": topic_id,
        "budget": {"queries_left": queries_left, "tokens_left": 4000},
        "events": sorted(visible_events, key=lambda row: row["event_id"]),
        "gaps": [{
            "gap_id": "g001",
            "type": gap_type,
            "priority": 1.0,
            "window_start": min(previous_date, target_date).isoformat(),
            "window_end": max(next_date, target_date).isoformat(),
        }],
        "valid_actions": ["SEARCH"],
    }


def query_copy_ratio(query: str, target_summary: str) -> float:
    query_tokens = content_tokens(query)
    target_tokens = content_tokens(target_summary)
    return len(query_tokens & target_tokens) / len(target_tokens) if target_tokens else 0.0


def validate_teacher_query(
    chosen: str,
    rejected: str,
    target_summary: str,
    maximum_copy_ratio: float,
) -> None:
    chosen_words = TOKEN.findall(chosen)
    rejected_words = TOKEN.findall(rejected)
    if not 3 <= len(chosen_words) <= 20:
        raise ValueError("Chosen query must contain 3 to 20 tokens")
    if not 2 <= len(rejected_words) <= 20:
        raise ValueError("Rejected query must contain 2 to 20 tokens")
    if chosen.strip().casefold() == rejected.strip().casefold():
        raise ValueError("Chosen and rejected queries must differ")
    if query_copy_ratio(chosen, target_summary) > maximum_copy_ratio:
        raise ValueError("Chosen query copies too much of the private gold summary")
