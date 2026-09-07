from __future__ import annotations


REQUIRED_SPLITS = ("train", "dev", "test")


def validate_topic_splits(splits: dict[str, list[str]]) -> dict[str, str]:
    missing = [name for name in REQUIRED_SPLITS if name not in splits]
    if missing:
        raise ValueError(f"Missing required splits: {missing}")
    assignment: dict[str, str] = {}
    for split_name in REQUIRED_SPLITS:
        topics = splits[split_name]
        if not isinstance(topics, list) or not topics:
            raise ValueError(f"Split {split_name!r} must contain at least one topic")
        for topic in topics:
            if not isinstance(topic, str) or not topic.strip():
                raise ValueError(f"Invalid topic in split {split_name!r}: {topic!r}")
            normalized = topic.strip()
            if normalized in assignment:
                raise ValueError(
                    f"Topic {normalized!r} appears in both "
                    f"{assignment[normalized]!r} and {split_name!r}"
                )
            assignment[normalized] = split_name
    return assignment


def row_topic(row: dict) -> str:
    topic = row.get("topic")
    if topic is None:
        topic = (row.get("metadata") or {}).get("topic")
    if not isinstance(topic, str) or not topic:
        raise ValueError("Training row does not contain a topic")
    return topic


def split_rows(rows: list[dict], assignment: dict[str, str]) -> dict[str, list[dict]]:
    output = {name: [] for name in REQUIRED_SPLITS}
    for row in rows:
        topic = row_topic(row)
        if topic not in assignment:
            raise ValueError(f"Topic {topic!r} has no split assignment")
        output[assignment[topic]].append(row)
    return output
