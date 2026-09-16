from __future__ import annotations

import json


ACTIONS = ("SEARCH", "VERIFY", "MERGE", "STOP")
CONTROLLER_ACTIONS = ("SEARCH", "SWITCH_PHASE", "GAP_MEMORY", "STOP")
ALL_ACTIONS = tuple(dict.fromkeys(ACTIONS + CONTROLLER_ACTIONS))

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "SEARCH",
            "description": "Search the frozen news corpus for evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "dataset": {"type": "string"},
                    "topic": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["query", "dataset", "topic", "top_k"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "VERIFY",
            "description": "Verify a candidate event against retrieved evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event": {"type": "object"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["event", "evidence_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "MERGE",
            "description": "Merge a verified event into the structured timeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event": {"type": "object"},
                    "operation": {"type": "string"},
                },
                "required": ["event", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "SWITCH_PHASE",
            "description": "Switch from skeleton exploration to gap refinement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "memory": {"type": "object"},
                },
                "required": ["reason", "memory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "GAP_MEMORY",
            "description": (
                "Initialize or update discontinuities, causal breaks, missing key "
                "factors, and evidence conflicts in the current ordered timeline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string"},
                    "gaps": {"type": "array", "items": {"type": "object"}},
                    "resolved_gap_ids": {
                        "type": "array", "items": {"type": "string"}
                    },
                },
                "required": ["thought", "gaps", "resolved_gap_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "STOP",
            "description": "Stop when the target gap is closed or further search has low value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["reason", "confidence"],
            },
        },
    },
]


def parse_json_object(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1]).strip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Teacher response does not contain a JSON object")
    payload = json.loads(value[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Teacher response must be a JSON object")
    return payload


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    if name not in ALL_ACTIONS:
        raise ValueError(f"Unknown TiSA action: {name}")
    return {
        "role": "assistant",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False, sort_keys=True),
            },
        }],
    }


def tool_observation(call_id: str, payload: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def validate_evidence_ids(evidence_ids: list[str], results: list[dict]) -> None:
    available = {str(item["id"]) for item in results}
    if not evidence_ids:
        raise ValueError("VERIFY must cite at least one evidence document")
    missing = sorted(set(map(str, evidence_ids)) - available)
    if missing:
        raise ValueError(f"VERIFY cites unavailable evidence IDs: {missing}")


def validate_preference_scores(scores: dict, margin: float) -> None:
    for action in ACTIONS:
        row = scores.get(action.lower())
        if not isinstance(row, dict):
            raise ValueError(f"Missing preference score for {action}")
        chosen, rejected = float(row["chosen"]), float(row["rejected"])
        if not 0 <= rejected <= chosen <= 1:
            raise ValueError(f"Invalid preference scores for {action}")
        if chosen - rejected < margin:
            raise ValueError(f"Preference margin is too small for {action}")
