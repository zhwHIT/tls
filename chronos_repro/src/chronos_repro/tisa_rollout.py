from __future__ import annotations

import copy
import re
from datetime import date

from .gap_contract import gap_signature, validate_atomic_gap


SKELETON = "SKELETON_EXPLORATION"
REFINE = "GAP_REFINEMENT"
GAP_TYPES = {
    "TEMPORAL_GAP",
    "CAUSAL_GAP",
    "MISSING_KEY_EVENT",
    "MISSING_FACTOR",
    "EVIDENCE_CONFLICT",
}
GAP_STATUSES = {"OPEN", "IN_PROGRESS", "RESOLVED", "NO_SEARCH_NEEDED", "FAILED", "DEFERRED"}
FORBIDDEN_LABEL_WORDS = (
    "gold",
    "reference answer",
    "ground truth",
    "\u6807\u51c6\u7b54\u6848",
    "\u53c2\u8003\u7b54\u6848",
    "\u91d1\u6807",
)


def event_date(event: dict) -> str:
    value = event.get("time")
    if isinstance(value, dict):
        value = value.get("value")
    result = str(value or "")[:10]
    date.fromisoformat(result)
    return result


def sort_events(events: list[dict]) -> list[dict]:
    """Return a defensive, deterministic chronological copy of timeline events."""
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    rows = copy.deepcopy(events)
    rows.sort(key=lambda row: (event_date(row), str(row.get("event_id", ""))))
    return rows


def validate_thought(value: object, maximum_chars: int = 240) -> str:
    """Validate a short, student-visible reason without supervision terminology."""
    if not isinstance(value, str):
        raise ValueError('thought must be a string')
    thought = re.sub(r"\s+", " ", value).strip()
    if len(thought) < 6 or len(thought) > maximum_chars:
        raise ValueError(f"thought must contain 6-{maximum_chars} characters")
    lowered = thought.casefold()
    if any((bool(re.search(r'(?<![a-z])gold(?![a-z])', lowered)) if marker == 'gold'
            else marker in lowered) for marker in FORBIDDEN_LABEL_WORDS):
        raise ValueError("thought exposes teacher-only supervision terminology")
    return thought


def student_state(
    dataset: str,
    topic: str,
    phase: str,
    events: list[dict],
    memory: dict,
    valid_actions: list[str],
    active_gap: dict | None = None,
) -> dict:
    """Build the exact model input. Runtime budgets are intentionally excluded."""
    state = {
        "schema_version": 4,
        "dataset": str(dataset),
        "topic": str(topic),
        "phase": str(phase),
        "events": sort_events(events),
        "memory": copy.deepcopy(memory),
        "valid_actions": list(valid_actions),
    }
    if active_gap is not None:
        state["active_gap"] = copy.deepcopy(active_gap)
    validate_student_state(state)
    return state


def validate_student_state(state: dict) -> None:
    serialized_keys: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            serialized_keys.update(map(str, value))
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(state)
    forbidden = {"budget", "query_budget", "queries_left", "rounds_left"}
    leaked = sorted(forbidden & serialized_keys)
    if leaked:
        raise ValueError(f"student state contains runtime budget fields: {leaked}")
    ordered = sort_events(state.get("events", []))
    if ordered != state.get("events", []):
        raise ValueError("events must be sorted chronologically")


def _validate_gap(row: dict, event_ids: set[str]) -> dict:
    gap_id = str(row.get("gap_id", "")).strip()
    if not gap_id:
        raise ValueError("gap_id is required")
    gap_type = str(row.get("type", "")).upper()
    if gap_type not in GAP_TYPES:
        raise ValueError(f"unsupported gap type: {gap_type}")
    raw_description = re.sub(
        r"\s+", " ",
        str(
            row.get("description")
            or row.get("reason")
            or row.get("thought")
            or ""
        ),
    ).strip()
    if len(raw_description) > 180:
        raise ValueError('gap description must contain 6-180 characters; rewrite briefly, do not truncate facts')
    description = validate_thought(raw_description, 180)
    priority = float(row.get("priority", 0.5))
    if not 0 <= priority <= 1:
        raise ValueError("gap priority must be between zero and one")
    status = str(row.get("status", "OPEN")).upper()
    if status not in GAP_STATUSES:
        raise ValueError(f"unsupported gap status: {status}")
    left = row.get("left_event_id")
    right = row.get("right_event_id")
    for neighbor in (left, right):
        if neighbor is not None and str(neighbor) not in event_ids:
            raise ValueError(f"gap neighbor does not exist: {neighbor}")
    result = {
        "gap_id": gap_id,
        "type": gap_type,
        "description": description,
        "priority": priority,
        "status": status,
        "left_event_id": str(left) if left is not None else None,
        "right_event_id": str(right) if right is not None else None,
        "attempted_queries": [str(query) for query in row.get("attempted_queries", [])],
    }
    if 'retrieval_target' in row:
        result['retrieval_target'] = copy.deepcopy(row['retrieval_target'])
    return result


def validate_gap_memory_action(
    payload: dict,
    events: list[dict],
    maximum_gaps: int,
    *,
    require_atomic: bool = False,
) -> dict:
    if str(payload.get("action", "")).upper() != "GAP_MEMORY":
        raise ValueError("gap initialization must use GAP_MEMORY")
    thought = validate_thought(payload.get("thought"))
    raw_gaps = payload.get("gaps")
    if not isinstance(raw_gaps, list) or len(raw_gaps) > maximum_gaps:
        raise ValueError("GAP_MEMORY gaps must be a bounded list")
    event_ids = {str(row.get("event_id")) for row in events}
    gaps = [validate_atomic_gap(_validate_gap(row, event_ids), events, required=require_atomic)
            for row in raw_gaps]
    if any(row["status"] != "OPEN" or row["attempted_queries"] for row in gaps):
        raise ValueError("New gaps must be OPEN with no invented attempted queries")
    ids = [row["gap_id"] for row in gaps]
    if len(ids) != len(set(ids)):
        raise ValueError("GAP_MEMORY gap IDs must be unique")
    signatures = [gap_signature(row) for row in gaps if 'retrieval_target' in row]
    if len(signatures) != len(set(signatures)):
        raise ValueError('duplicate atomic gaps must be consolidated before scheduling')
    return {"thought": thought, "action": "GAP_MEMORY", "gaps": gaps}


def validate_gap_search_action(payload: dict, active_gap: dict) -> dict:
    thought = validate_thought(payload.get("thought"))
    if str(payload.get("action", "")).upper() != "SEARCH":
        raise ValueError("each gap analysis must emit SEARCH")
    if str(payload.get("gap_id", "")) != str(active_gap["gap_id"]):
        raise ValueError("SEARCH gap_id must match active_gap")
    if not isinstance(payload.get('query'), str):
        raise ValueError('gap SEARCH query must be a string; use empty string to skip')
    query = payload['query'].strip()
    if query and not 3 <= len(re.findall(r"[A-Za-z0-9]+", query)) <= 20:
        raise ValueError("non-empty gap SEARCH query must contain 3-20 tokens")
    return {
        "thought": thought,
        "action": "SEARCH",
        "gap_id": str(active_gap["gap_id"]),
        "query": query,
    }


def apply_gap_update(
    gap_memory: dict,
    active_gap_id: str,
    query: str,
    resolved_gap_ids: list[str],
    new_gaps: list[dict],
    events: list[dict],
    maximum_gaps: int,
    *,
    maximum_attempts: int | None = None,
) -> tuple[dict, dict]:
    """Apply one SEARCH/VERIFY/MERGE cycle and record newly exposed gaps."""
    memory = copy.deepcopy(gap_memory)
    if maximum_attempts is not None and (type(maximum_attempts) is not int or maximum_attempts < 1):
        raise ValueError('maximum_attempts must be a positive integer')
    gaps = list(memory.get("gaps", []))
    by_id = {str(row["gap_id"]): row for row in gaps}
    if active_gap_id not in by_id:
        raise ValueError("active gap is absent from gap memory")
    if query:
        by_id[active_gap_id].setdefault("attempted_queries", []).append(query)
        by_id[active_gap_id]["status"] = "IN_PROGRESS"
    else:
        by_id[active_gap_id]["status"] = "NO_SEARCH_NEEDED"
    resolved = list(dict.fromkeys(map(str, resolved_gap_ids)))
    unknown = sorted(set(resolved) - set(by_id))
    if unknown:
        raise ValueError(f"resolved gap IDs are unknown: {unknown}")
    for gap_id in resolved:
        by_id[gap_id]["status"] = "RESOLVED"
    active = by_id[active_gap_id]
    if (query and active['status'] != 'RESOLVED' and maximum_attempts is not None
            and len(active.get('attempted_queries', [])) >= maximum_attempts):
        active['status'] = 'DEFERRED'
        active['deferred_reason'] = 'per_gap_attempt_limit_not_resolution'

    event_ids = {str(row.get("event_id")) for row in events}
    additions = [validate_atomic_gap(_validate_gap(row, event_ids), events) for row in new_gaps]
    if any(row["status"] != "OPEN" or row["attempted_queries"] for row in additions):
        raise ValueError("New gaps must be OPEN with no invented attempted queries")
    added = []
    duplicates = []
    for row in additions:
        if row["gap_id"] in by_id:
            continue
        existing = next((g for g in gaps if 'retrieval_target' in row and 'retrieval_target' in g
                         and gap_signature(g) == gap_signature(row)), None)
        if existing is not None:
            memory.setdefault('duplicate_gap_proposals', []).append({
                'existing_gap_id': existing['gap_id'], 'proposal': copy.deepcopy(row)})
            duplicates.append(row['gap_id'])
            continue
        gaps.append(row)
        by_id[row["gap_id"]] = row
        added.append(row["gap_id"])
    memory["gaps"] = gaps
    # Keep every discovered gap, including overflow; a capacity limit is not resolution.
    unresolved = [r["gap_id"] for r in gaps if r["status"] in {"OPEN", "IN_PROGRESS", "FAILED", "DEFERRED"}]
    memory["queue_capacity"] = maximum_gaps
    memory["overflow_gap_ids"] = unresolved[maximum_gaps:]
    memory.setdefault("history", []).append({
        "active_gap_id": active_gap_id,
        "query": query,
        "resolved_gap_ids": resolved,
        "new_gap_ids": added,
    })
    audit = {"resolved_gap_ids": resolved, "new_gap_ids": added}
    if duplicates:
        audit['deduplicated_gap_ids'] = duplicates
    if active['status'] == 'DEFERRED':
        audit['deferred_gap_ids'] = [active_gap_id]
        audit['completion_established'] = False
    return memory, audit


def next_open_gap(gap_memory: dict) -> dict | None:
    candidates = [
        row for row in gap_memory.get("gaps", []) if row.get("status") == "OPEN"
    ]
    return max(
        candidates,
        # Round-robin by attempts, then priority. Retrying a high-priority gap
        # cannot starve untried gaps. OPEN remains OPEN; scheduling is not resolution.
        key=lambda row: (-len(row.get("attempted_queries", [])),
                         float(row.get("priority", 0)), row["gap_id"]),
        default=None,
    )
