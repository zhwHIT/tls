from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from .data import iter_topics, load_prediction
from .evaluate import evaluate_dates, evaluate_tilse
from .provenance import runtime_metadata
from .snapshot import sha256


def _numeric_mean(values: list[dict]) -> dict:
    keys = values[0].keys()
    output = {}
    for key in keys:
        children = [value[key] for value in values]
        if all(isinstance(child, dict) for child in children):
            output[key] = _numeric_mean(children)
        elif all(isinstance(child, (int, float)) and not isinstance(child, bool) for child in children):
            output[key] = sum(children) / len(children)
    return output


def evaluate_directory(
    data_root: str | Path,
    predictions: str | Path,
    rouge_backend: str = "original",
    date_only: bool = False,
    allow_missing: bool = False,
    upstream_commit: str | None = None,
) -> dict:
    started = perf_counter()
    predictions = Path(predictions).resolve()
    rows = []
    missing = []
    for topic in iter_topics(data_root):
        path = predictions / f"{topic.topic_id}.json"
        if not path.is_file():
            missing.append(topic.topic_id)
            continue
        prediction = load_prediction(path)
        metrics = (
            {"date_score": asdict(evaluate_dates(prediction, topic.timelines))}
            if date_only
            else evaluate_tilse(prediction, topic.timelines, rouge_backend)
        )
        metrics.pop("rouge_backend", None)
        rows.append(
            {
                "topic_id": topic.topic_id,
                "prediction": str(path),
                "prediction_sha256": sha256(path),
                "metrics": metrics,
            }
        )
    if missing and not allow_missing:
        raise FileNotFoundError(
            f"Missing {len(missing)} predictions named <topic>.json: {', '.join(missing)}"
        )
    if not rows:
        raise ValueError(f"No matching predictions found in {predictions}")
    return {
        "schema_version": 1,
        "evaluated_topics": len(rows),
        "missing_topics": missing,
        "macro_topic_mean": _numeric_mean([row["metrics"] for row in rows]),
        "topics": rows,
        "provenance": {
            **runtime_metadata(data_root, "date-only" if date_only else rouge_backend, upstream_commit),
            "predictions_root": str(predictions),
            "duration_seconds": perf_counter() - started,
        },
    }
