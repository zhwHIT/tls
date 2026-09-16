"""Publication-date actions grounded in the current, policy-visible timeline."""
from __future__ import annotations

import copy
from datetime import date, timedelta

from .temporal_filter import validate_date_filter
from .tisa_rollout import event_date, validate_student_state


def temporal_enabled(config: dict) -> bool:
    return bool(config.get("temporal_search", {}).get("enabled", False))


def visible_temporal_state(visible: dict, config: dict) -> dict:
    state = copy.deepcopy(visible)
    if not temporal_enabled(config):
        return state
    settings = config["temporal_search"]
    anchors = [{"event_id": str(e["event_id"]), "date": event_date(e)} for e in state["events"]]
    by_id = {a["event_id"]: a["date"] for a in anchors}
    for gap in state["memory"].get("gaps", []):
        ids = [gap.get("left_event_id"), gap.get("right_event_id")]
        known = [by_id[i] for i in ids if i in by_id]
        gap["temporal_context"] = {
            "anchor_event_ids": list(dict.fromkeys(i for i in ids if i in by_id)),
            "date_from": min(known) if known else None,
            "date_to": max(known) if known else None,
            "source": "current_timeline_neighbors",
        }
    if state.get("active_gap"):
        matching = next((g for g in state["memory"].get("gaps", []) if g["gap_id"] == state["active_gap"]["gap_id"]), None)
        if matching:
            state["active_gap"] = copy.deepcopy(matching)
    state["schema_version"] = 5
    state["retrieval_contract"] = {
        "date_field": "document_publication_date",
        "metadata_warning": "Publication dates are not event boundaries; updated aggregation pages can contain later events, and retrospective pages earlier events.",
        "modes": ["none", "soft", "hard"],
        "visible_anchors": anchors,
        "max_padding_days": settings.get("max_padding_days", 90),
        "suggested_padding_days": settings.get("suggested_padding_days", 14),
        "rule": "Select visible anchor IDs; bounds equal min/max anchor dates plus padding. Prefer soft; hard requires two distinct anchor dates. Empty query requires none.",
    }
    validate_student_state(state)
    return state


DATE_OUTPUT_SCHEMA = {
    "mode": "none|soft|hard",
    "date_from": "YYYY-MM-DD or null for none",
    "date_to": "YYYY-MM-DD or null for none",
    "anchor_event_ids": ["ID from current events; [] for none"],
    "padding_days": "integer 0..max_padding_days; 0 for none",
}

DATE_PROMPT = (
    "Choose date_filter using only current visible events and memory. Dates constrain "
    "document publication, not event occurrence. Select anchor_event_ids from events; "
    "Updated aggregation pages can contain events later than their publication metadata; "
    "neither the minimum nor maximum publication timestamp establishes event coverage or a STOP boundary. "
    "date_from=min(anchor dates)-padding_days and date_to=max(anchor dates)+padding_days. "
    "Prefer soft to retain retrospective evidence. Use none if no useful time anchor "
    "exists or earlier bounded searches failed; none uses null bounds, [] anchors, 0 padding. "
    "Use hard only for a justified local interval with two distinct visible anchor dates. "
    "An empty query requires none. Do not copy dates from private targets. Explain the "
    "search intention briefly in thought. Consult memory history to avoid repeating "
    "the same query and range without new evidence."
)


def validate_temporal_action(action: dict, visible: dict, config: dict) -> dict:
    if not temporal_enabled(config):
        return action
    raw = action.get("date_filter")
    if not isinstance(raw, dict):
        raise ValueError("date_filter must be an object")
    required = {"mode", "date_from", "date_to", "anchor_event_ids", "padding_days"}
    if set(raw) != required:
        raise ValueError("date_filter must contain exactly mode, date_from, date_to, anchor_event_ids, padding_days")
    mode = raw["mode"]
    ids = raw["anchor_event_ids"]
    padding = raw["padding_days"]
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("anchor_event_ids must be unique strings")
    max_padding = config["temporal_search"].get("max_padding_days", 90)
    if type(padding) is not int or not 0 <= padding <= max_padding:
        raise ValueError("padding_days is outside the configured range")
    for name in ("date_from", "date_to"):
        if raw[name] is not None and not isinstance(raw[name], str):
            raise ValueError(f"{name} must be an ISO date string or null")
    validate_date_filter(raw["date_from"], raw["date_to"], mode, config["temporal_search"].get("soft_penalty", 0.5))
    if mode == "none":
        if ids or padding or raw["date_from"] is not None or raw["date_to"] is not None:
            raise ValueError("none requires null dates, empty anchors and zero padding")
    else:
        if not action["query"]:
            raise ValueError("empty query requires date_filter mode none")
        by_id = {str(e["event_id"]): event_date(e) for e in visible["events"]}
        if not ids or any(i not in by_id for i in ids):
            raise ValueError("date anchors must exist in current visible events")
        days = [date.fromisoformat(by_id[i]) for i in ids]
        if mode == "hard" and len(set(days)) < 2:
            raise ValueError("hard requires two distinct visible anchor dates")
        try:
            expected = ((min(days) - timedelta(days=padding)).isoformat(), (max(days) + timedelta(days=padding)).isoformat())
        except OverflowError as exc:
            raise ValueError("date padding exceeds calendar bounds") from exc
        if (raw["date_from"], raw["date_to"]) != expected:
            raise ValueError(f"date bounds must follow visible anchors and padding: {expected}")
    return {**action, "date_filter": copy.deepcopy(raw)}


def retrieval_kwargs(action: dict, config: dict) -> dict:
    raw = action.get("date_filter")
    if raw is None:
        return {}
    return {
        "date_from": raw["date_from"], "date_to": raw["date_to"],
        "date_filter_mode": raw["mode"],
        "date_soft_penalty": config.get("temporal_search", {}).get("soft_penalty", 0.5),
    }


def remember_temporal_search(memory: dict, gap_id: str, decision: dict, results: list[dict], config: dict) -> None:
    if not temporal_enabled(config):
        return
    record = {
        "query": decision["query"], "date_filter": copy.deepcopy(decision["date_filter"]),
        "retrieval_parameters": retrieval_kwargs(decision, config),
        "result_ids": [str(r["id"]) for r in results],
        "returned_documents": len(results), "skipped": not bool(decision["query"]),
    }
    gap = next(g for g in memory["gaps"] if g["gap_id"] == gap_id)
    gap.setdefault("search_attempts", []).append(copy.deepcopy(record))
    memory.setdefault("search_history", []).append({"gap_id": gap_id, **record})
