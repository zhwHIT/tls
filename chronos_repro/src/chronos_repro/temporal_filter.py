from __future__ import annotations

from datetime import date


DATE_FILTER_MODES = ("none", "soft", "hard")


def validate_date_filter(
    date_from: str | None,
    date_to: str | None,
    mode: str,
    soft_penalty: float,
) -> tuple[str | None, str | None]:
    """Validate an optional publication-date constraint."""
    if mode not in DATE_FILTER_MODES:
        raise ValueError(f"date_filter_mode must be one of {DATE_FILTER_MODES}")
    if soft_penalty < 0:
        raise ValueError("date_soft_penalty must be non-negative")
    if mode == "none":
        if date_from or date_to:
            raise ValueError("date bounds require date_filter_mode soft or hard")
        return None, None
    if not date_from and not date_to:
        raise ValueError("soft/hard date filtering requires date_from or date_to")
    parsed_from = _parse_bound(date_from, "date_from")
    parsed_to = _parse_bound(date_to, "date_to")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise ValueError("date_from must not be later than date_to")
    return date_from, date_to


def _parse_bound(value: str | None, name: str) -> date | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return parsed


def in_publication_range(
    timestamp: str | None,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    value = str(timestamp or "")[:10]
    try:
        publication_date = date.fromisoformat(value)
    except ValueError:
        return False
    if date_from and publication_date < date.fromisoformat(date_from):
        return False
    if date_to and publication_date > date.fromisoformat(date_to):
        return False
    return True


def apply_date_filter(
    items: list[dict],
    limit: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    mode: str = "none",
    soft_penalty: float = 0.5,
) -> list[dict]:
    """Filter or softly demote evidence outside a publication-date range."""
    validate_date_filter(date_from, date_to, mode, soft_penalty)
    if mode == "none":
        return items[:limit]
    decorated: list[tuple[float, int, dict, bool]] = []
    penalty_slots = max(1, round(len(items) * soft_penalty))
    for rank, source in enumerate(items, start=1):
        inside = in_publication_range(source.get("timestamp"), date_from, date_to)
        if mode == "hard" and not inside:
            continue
        adjusted_rank = float(rank + (0 if inside else penalty_slots))
        decorated.append((adjusted_rank, rank, source, inside))
    decorated.sort(key=lambda row: (row[0], not row[3], row[1]))
    output = []
    for adjusted_rank, original_rank, source, inside in decorated[:limit]:
        item = dict(source)
        retrieval = dict(item.get("retrieval") or {})
        retrieval["date_filter"] = {
            "mode": mode,
            "date_from": date_from,
            "date_to": date_to,
            "in_range": inside,
            "original_rank": original_rank,
            "adjusted_rank": adjusted_rank,
            "soft_penalty": soft_penalty if mode == "soft" else None,
        }
        item["retrieval"] = retrieval
        output.append(item)
    return output


def attach_date_filter_metadata(
    items: list[dict],
    *,
    date_from: str | None,
    date_to: str | None,
    mode: str,
    soft_penalty: float,
) -> list[dict]:
    """Attach final audit metadata without applying the soft penalty twice."""
    if mode == "none":
        return items
    output = []
    for source in items:
        item = dict(source)
        retrieval = dict(item.get("retrieval") or {})
        retrieval["date_filter"] = {
            "mode": mode,
            "date_from": date_from,
            "date_to": date_to,
            "in_range": in_publication_range(item.get("timestamp"), date_from, date_to),
            "soft_penalty": soft_penalty if mode == "soft" else None,
        }
        item["retrieval"] = retrieval
        output.append(item)
    return output
