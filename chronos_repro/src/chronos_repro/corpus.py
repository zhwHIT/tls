from __future__ import annotations

import gzip
import json
from pathlib import Path

from .data import load_timelines


DATASET_TOPICS = {
    "crisis": {"egypt", "libya", "syria", "yemen"},
    "t17": {"bpoil", "egypt", "finan", "h1n1", "haiti", "iraq", "libya", "mj", "syria"},
}
EXPECTED_TOPIC_COUNTS = {"crisis": 4, "t17": 9, "entities": 47}
REQUIRED_FILES = ("articles.preprocessed.jsonl.gz", "keywords.json", "timelines.jsonl")
ARTICLE_FIELDS = {"id", "time", "title", "text", "sentences"}


def validate_corpus(root: str | Path, dataset: str, full: bool = True) -> dict:
    """Validate corpus layout and parse every record (or only the first in quick mode)."""
    root = Path(root).resolve()
    if dataset not in EXPECTED_TOPIC_COUNTS:
        raise ValueError(f"Unknown dataset: {dataset}")
    topic_dirs = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())
    names = {p.name for p in topic_dirs}
    errors: list[str] = []
    expected_names = DATASET_TOPICS.get(dataset)
    if len(topic_dirs) != EXPECTED_TOPIC_COUNTS[dataset]:
        errors.append(
            f"topic count mismatch: expected {EXPECTED_TOPIC_COUNTS[dataset]}, found {len(topic_dirs)}"
        )
    if expected_names is not None and names != expected_names:
        errors.append(
            f"topic names mismatch: missing={sorted(expected_names - names)}, extra={sorted(names - expected_names)}"
        )

    items = []
    for topic_dir in topic_dirs:
        topic_errors: list[str] = []
        missing = [name for name in REQUIRED_FILES if not (topic_dir / name).is_file()]
        if missing:
            topic_errors.append(f"missing files: {missing}")
        article_count = 0
        article_path = topic_dir / REQUIRED_FILES[0]
        if article_path.is_file():
            try:
                with gzip.open(article_path, "rt", encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, 1):
                        if not line.strip():
                            continue
                        article = json.loads(line)
                        if not isinstance(article, dict):
                            raise ValueError(f"line {line_no} is not an object")
                        absent = ARTICLE_FIELDS - article.keys()
                        if absent:
                            raise ValueError(f"line {line_no} missing fields {sorted(absent)}")
                        article_count += 1
                        if not full:
                            break
                if article_count == 0:
                    raise ValueError("no articles")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                topic_errors.append(f"invalid articles: {error}")
        keywords_path = topic_dir / REQUIRED_FILES[1]
        if keywords_path.is_file():
            try:
                keywords = json.loads(keywords_path.read_text(encoding="utf-8-sig"))
                if not isinstance(keywords, list) or not keywords or not all(
                    isinstance(value, str) and value for value in keywords
                ):
                    raise ValueError("must be a non-empty string list")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                topic_errors.append(f"invalid keywords: {error}")
        timeline_count = 0
        timeline_path = topic_dir / REQUIRED_FILES[2]
        if timeline_path.is_file():
            try:
                timeline_count = len(load_timelines(timeline_path))
            except (OSError, UnicodeError, ValueError) as error:
                topic_errors.append(f"invalid timelines: {error}")
        errors.extend(f"{topic_dir.name}: {error}" for error in topic_errors)
        items.append(
            {
                "topic_id": topic_dir.name,
                "valid": not topic_errors,
                "article_records_checked": article_count,
                "reference_timelines": timeline_count,
                "errors": topic_errors,
            }
        )
    return {
        "dataset": dataset,
        "root": str(root),
        "mode": "full" if full else "quick",
        "valid": not errors,
        "expected_topics": EXPECTED_TOPIC_COUNTS[dataset],
        "found_topics": len(topic_dirs),
        "errors": errors,
        "items": items,
    }
