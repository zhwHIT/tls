from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Topic:
    topic_id: str
    query: str | None
    timelines: tuple[dict[date, tuple[str, ...]], ...]


def parse_date(value: str) -> date:
    """Parse CHRONOS date strings, mapping partial dates to their first day."""
    raw = value.strip()
    if raw.endswith("T00:00:00"):
        raw = raw[:-9]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported timeline date: {value!r}")


def _read_jsonish(line: str, path: Path, line_no: int) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError as json_error:
        # Some legacy TLS corpora use Python literal syntax. literal_eval is safe;
        # arbitrary eval, used upstream, is intentionally not reproduced.
        try:
            return ast.literal_eval(line)
        except (ValueError, SyntaxError) as literal_error:
            raise ValueError(
                f"Invalid JSON/literal in {path}:{line_no}: {json_error}"
            ) from literal_error


def load_timelines(path: str | Path) -> tuple[dict[date, tuple[str, ...]], ...]:
    path = Path(path)
    output: list[dict[date, tuple[str, ...]]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            raw_timeline = _read_jsonish(line, path, line_no)
            timeline: dict[date, tuple[str, ...]] = {}
            if not isinstance(raw_timeline, list):
                raise ValueError(f"Timeline must be a list in {path}:{line_no}")
            for item in raw_timeline:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    raise ValueError(f"Invalid event pair in {path}:{line_no}: {item!r}")
                timestamp, events = item
                event_list = [events] if isinstance(events, str) else events
                if not isinstance(event_list, (list, tuple)) or not all(
                    isinstance(event, str) for event in event_list
                ):
                    raise ValueError(f"Events must be strings in {path}:{line_no}")
                parsed = parse_date(str(timestamp))
                timeline[parsed] = timeline.get(parsed, ()) + tuple(event_list)
            if timeline:
                output.append(dict(sorted(timeline.items())))
    if not output:
        raise ValueError(f"No non-empty timelines found in {path}")
    return tuple(output)


def load_queries(path: str | Path | None) -> dict[str, str]:
    """Load {topic_id: query} from JSON; query metadata is optional."""
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        raise ValueError("Query file must be a JSON object mapping topic IDs to strings")
    return payload


def iter_topics(root: str | Path, queries: dict[str, str] | None = None) -> Iterable[Topic]:
    root = Path(root)
    queries = queries or {}
    files = sorted(root.glob("*/timelines.jsonl"), key=lambda p: p.parent.name.casefold())
    if not files:
        raise FileNotFoundError(f"No */timelines.jsonl files below {root}")
    for path in files:
        topic_id = path.parent.name
        yield Topic(topic_id, queries.get(topic_id), load_timelines(path))


def load_prediction(path: str | Path) -> dict[date, tuple[str, ...]]:
    """Read CHRONOS saved output or a raw Open-TLS-style timeline."""
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and "predict-timeline" in payload:
        payload = payload["predict-timeline"]
    timeline: dict[date, tuple[str, ...]] = {}
    if not isinstance(payload, list):
        raise ValueError(f"Prediction must be a list in {path}")
    for item in payload:
        if isinstance(item, dict):
            timestamp = item.get("start")
            events = item.get("events", item.get("summary"))
        elif isinstance(item, list) and len(item) == 2:
            timestamp, events = item
        else:
            raise ValueError(f"Invalid prediction item in {path}: {item!r}")
        event_list = [events] if isinstance(events, str) else events
        if timestamp is None or not isinstance(event_list, list) or not all(
            isinstance(event, str) for event in event_list
        ):
            raise ValueError(f"Invalid prediction fields in {path}: {item!r}")
        parsed = parse_date(str(timestamp))
        timeline[parsed] = timeline.get(parsed, ()) + tuple(event_list)
    return dict(sorted(timeline.items()))
