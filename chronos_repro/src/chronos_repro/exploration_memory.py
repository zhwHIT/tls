"""Evidence-backed exploration notes, separate from the verified timeline."""
from __future__ import annotations

import copy
import json
import re
from datetime import date
from pathlib import Path

from .tisa_rollout import SKELETON, validate_thought
from .date_evidence import explicit_dates
from .label_repair import BOUNDARY_MAX_CHARS
from .exploration_control import check_search_progress, validate_deferred_directions


EXPLORATION_PROMPT = (
    "Build a coarse chronological skeleton, not a detailed causal-gap analysis. "
    "Disambiguate the topic using supplied dataset keywords and retrieved evidence, "
    "not a generic history of the topic name. Begin with a broad discovery query. "
    "Then diverge using observed actors, terms, event dates and stages: probe earlier "
    "developments, later developments, underrepresented intervals and different facets. "
    "Do not treat the latest retrieved event as the endpoint without testing for later "
    "developments. Prefer milestones across stages over many details on one day. "
    "Publication dates are not event dates. Memory observations are provisional leads, "
    "not verified facts. A hypothesis is a search direction, never an observed event. "
    "Use completed search history: new_verified_event_count and evidence_update_count, "
    "not just document/observation novelty. Do not repeat one strategy more than three "
    "times consecutively. Change the event, interval or facet after repeated no-gain "
    "queries. Two comparable no-gain probes can justify explicitly deferring an "
    "unconfirmed direction; deferral is not evidence that the event never occurred. "
    "Publication-date limits never establish the beginning/end of event content. "
    "No result is guaranteed for a proposed period; record unsuccessful probes honestly. "
    "STOP only when the coarse beginning, intermediate stages and latest known outcome "
    "are represented, boundary probes have been tried, and no actionable major exploration lead remains. "
    "Keep deferred unconfirmed leads visible; coarse readiness never asserts exhaustive coverage."
)

# Fictional, unrelated demonstrations of diversification, not target-specific answers.
FEW_SHOTS = [
    {"topic": "Aurora mission", "keywords": ["Aurora", "probe"],
     "observed": "A report describes a 2020 launch and mentions a planned 2022 arrival.",
     "queries": ["Aurora probe development before 2020 launch",
                 "Aurora probe arrival results 2022",
                 "Aurora probe later mission status"],
     "lesson": "Search backward, forward and for outcomes. A planned arrival is not proof it occurred."},
    {"topic": "Harbor rail project", "keywords": ["Harbor", "rail"],
     "observed": "Reports cover approval in 2015 and opening in 2021, but little in between.",
     "queries": ["Harbor rail construction progress 2017 2019",
                 "Harbor rail funding route changes",
                 "Harbor rail service after opening"],
     "lesson": "Vary interval and facet; dates must come from the real task evidence, not this example."},
]


def load_keywords(topic_path: Path) -> list[str]:
    path = topic_path / "keywords.json"
    if not path.exists():
        return []
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError("keywords.json must be a list of strings")
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def initial_memory(keywords: list[str]) -> dict:
    return {"phase": SKELETON, "keywords": list(keywords), "observed_events": [],
            "discovered_keywords": [], "search_history": [], "stage_outline": [],
            "next_search_directions": [], "boundary_assessment": "Not explored yet.",
            "skeleton_ready": False, "observed_date_range": None}


def _short(value: object, name: str, limit: int = 400) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be nonempty text <= {limit} characters")
    return value.strip()


def _period(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", value):
        raise ValueError("event_time must be YYYY, YYYY-MM, YYYY-MM-DD or null")
    date.fromisoformat(value + {4: "-01-01", 7: "-01", 10: ""}[len(value)])
    return value


def validate_memory_output(payload: dict, memory: dict, documents: list[dict]) -> dict:
    if payload.get("action") != "MEMORY_UPDATE":
        raise ValueError("expected MEMORY_UPDATE")
    thought = validate_thought(payload.get("thought"))
    rows = payload.get("observations")
    if not isinstance(rows, list) or len(rows) > 16:
        raise ValueError("observations must be a list of at most 16")
    by_id = {str(d["id"]): d for d in documents}
    observations = []
    for row in rows:
        doc_id = str(row.get("document_id", ""))
        if doc_id not in by_id:
            raise ValueError("observation cites a document not in this retrieval")
        quote = _short(row.get("evidence_quote"), "evidence_quote", 500)
        normalize = lambda s: re.sub(r"\s+", " ", str(s)).strip()
        document = by_id[doc_id]
        text = normalize(str(document.get("title", "")) + " " + str(document.get("text", "")))
        if normalize(quote) not in text:
            raise ValueError("evidence_quote must occur verbatim in supplied title/text")
        event_time = _period(row.get("event_time"))
        time_expression = row.get("time_expression")
        if event_time is not None or time_expression is not None:
            time_expression = _short(time_expression, "time_expression", 100)
            if normalize(time_expression) not in normalize(quote):
                raise ValueError("time_expression must occur in the evidence_quote")
        if memory.get("progress_control_enabled") and event_time is not None and event_time not in explicit_dates(time_expression):
            raise ValueError("Memory date precision/value must match literal time_expression; retain null for unsupported relative dates")
        observations.append({"summary": _short(row.get("summary"), "summary"),
                             "event_time": event_time, "time_expression": time_expression,
                             "document_id": doc_id, "evidence_quote": quote,
                             "publication_date": document.get("publication_date"),
                             "verification_status": "PROVISIONAL"})
    keywords = payload.get("discovered_keywords")
    if not isinstance(keywords, list):
        raise ValueError("discovered_keywords must be a list")
    available_text = " ".join(str(d.get("title", "")) + " " + str(d.get("text", "")) for d in documents).casefold()
    for word in keywords:
        _short(word, "keyword", 100)
        if word.casefold() not in available_text:
            raise ValueError("discovered keyword must occur in supplied documents")
    keywords = list(dict.fromkeys(keywords))[:20]
    # Ordinal IDs are assigned by the runner, never invented by the policy.
    outline = copy.deepcopy(payload.get("stage_outline"))
    known_docs = (set(by_id) | {str(o["document_id"]) for o in memory["observed_events"]}
                  | {str(doc_id) for search in memory.get("search_history", [])
                     for doc_id in search.get("result_ids", [])})
    if not isinstance(outline, list) or len(outline) > 12:
        raise ValueError("stage_outline must be a list of at most 12")
    for stage in outline:
        _short(stage.get("description"), "stage description")
        _short(stage.get("period"), "stage period", 100)
        ids = stage.get("evidence_ids")
        if isinstance(ids, list):
            provenance = memory.get('verified_event_sources', {})
            ids = list(dict.fromkeys(source for identity in ids for source in provenance.get(identity, [identity])))
            stage['evidence_ids'] = ids
        if not isinstance(ids, list) or not ids or not set(ids).issubset(known_docs):
            raise ValueError("stage must cite current or remembered document IDs")
    directions = payload.get("next_search_directions")
    if not isinstance(directions, list) or len(directions) > 8:
        raise ValueError("next_search_directions must contain at most 8 strings")
    for direction in directions:
        _short(direction, "search direction")
    ready = payload.get("skeleton_ready")
    if type(ready) is not bool or (ready and (directions or not outline)):
        raise ValueError("skeleton_ready requires an outline and no remaining coarse search directions")
    deferred = validate_deferred_directions(payload.get("deferred_directions", []), memory) if memory.get("progress_control_enabled") else []
    if any(row["direction"] in directions for row in deferred):
        raise ValueError("A direction cannot be both pending and deferred")
    return {"thought": thought, "action": "MEMORY_UPDATE", "observations": observations,
            **({"deferred_directions": deferred} if memory.get("progress_control_enabled") else {}),
            "discovered_keywords": keywords, "stage_outline": copy.deepcopy(outline),
            "next_search_directions": directions, "skeleton_ready": ready,
            "boundary_assessment": _short(payload.get("boundary_assessment"), "boundary_assessment", BOUNDARY_MAX_CHARS)}


def apply_memory_output(memory: dict, output: dict, query: str, strategy: str, documents: list[dict]) -> dict:
    result = copy.deepcopy(memory)
    seen = {(o["document_id"], o["event_time"], o["summary"].casefold()) for o in result["observed_events"]}
    added = 0
    for observation in output["observations"]:
        key = (observation["document_id"], observation["event_time"], observation["summary"].casefold())
        if key not in seen:
            seen.add(key)
            result["observed_events"].append({"observation_id": f"obs-{len(result['observed_events']) + 1:04d}", **observation})
            added += 1
    result["observed_events"].sort(key=lambda o: (o["event_time"] or "9999", o["observation_id"]))
    previous_ids = {i for h in result["search_history"] for i in h["result_ids"]}
    ids = [str(d["id"]) for d in documents]
    result["search_history"].append({"query": query, "strategy": strategy, "result_ids": ids,
                                     "new_document_count": len(set(ids) - previous_ids),
                                     "new_observation_count": added})
    result["discovered_keywords"] = list(dict.fromkeys(result["discovered_keywords"] + output["discovered_keywords"]))
    for key in ("stage_outline", "next_search_directions", "skeleton_ready", "boundary_assessment"):
        result[key] = copy.deepcopy(output[key])
    times = [o["event_time"] for o in result["observed_events"] if o["event_time"]]
    if result.get("progress_control_enabled"):
        by_direction = {r["direction"]: r for r in result.get("deferred_directions", [])}
        by_direction.update({r["direction"]: copy.deepcopy(r) for r in output.get("deferred_directions", [])})
        result["deferred_directions"] = [r for key, r in by_direction.items() if key not in result["next_search_directions"]]
    result["observed_date_range"] = {"from": min(times), "to": max(times)} if times else None
    return result


def filter_invalid_memory_observations(payload: dict, memory: dict, documents: list[dict]) -> tuple[dict, list[dict]]:
    """After full retries, reject bad rows without weakening evidence/date checks."""
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        raise ValueError("cannot filter a non-structured memory response")
    accepted, rejected = [], []
    for index, observation in enumerate(payload["observations"]):
        probe = {**payload, "thought": "Validate one cited observation independently.",
                 "observations": [observation], "discovered_keywords": [], "stage_outline": [],
                 "next_search_directions": [], "skeleton_ready": False}
        try:
            validate_memory_output(probe, memory, documents)
            accepted.append(observation)
        except (ValueError, KeyError, TypeError) as error:
            rejected.append({"kind": "observation", "observation_index": index,
                             "reason": str(error), "raw_observation": observation})
    if payload["observations"] and not accepted:
        raise ValueError("all memory observations failed; do not manufacture an empty accepted label")
    keywords = payload.get("discovered_keywords")
    if not isinstance(keywords, list):
        raise ValueError("discovered_keywords must be a list")
    available_text = " ".join(str(d.get("title", "")) + " " + str(d.get("text", "")) for d in documents).casefold()
    accepted_keywords = []
    for word in keywords:
        try:
            _short(word, "keyword", 100)
            if word.casefold() not in available_text:
                raise ValueError("discovered keyword must occur in supplied documents")
            accepted_keywords.append(word)
        except (ValueError, TypeError) as error:
            rejected.append({"kind": "keyword", "raw_keyword": word, "reason": str(error)})
    # Keep the model's original thought, outline and directions: these must
    # independently satisfy the normal global validator; no invented replacement.
    return validate_memory_output({**payload, "observations": accepted,
                                   "discovered_keywords": accepted_keywords}, memory, documents), rejected


def validate_exploration_policy(payload: dict, visible: dict) -> dict:
    thought = validate_thought(payload.get("thought"))
    action = payload.get("action")
    if action not in visible["valid_actions"]:
        raise ValueError("action is not currently allowed")
    query = payload.get("query")
    strategy = payload.get("strategy")
    if action == "SEARCH":
        if not isinstance(query, str) or not 3 <= len(re.findall(r"[A-Za-z0-9]+", query)) <= 20:
            raise ValueError("SEARCH needs a 3-20 token English query")
        previous = {re.sub(r"\s+", " ", h["query"]).strip().casefold() for h in visible["memory"]["search_history"]}
        if re.sub(r"\s+", " ", query).strip().casefold() in previous:
            raise ValueError("query repeats an earlier search")
        if strategy not in {"DISCOVER", "EARLIER", "LATER", "INTERVAL", "FACET", "OVERVIEW"}:
            raise ValueError("unsupported exploration strategy")
        check_search_progress(visible["memory"], query, strategy)
    elif action == "STOP":
        if query != "" or strategy != "COMPLETE" or not visible["memory"]["skeleton_ready"]:
            raise ValueError("STOP needs empty query, COMPLETE strategy and ready skeleton memory")
    return {"thought": thought, "action": action, "query": query.strip(), "strategy": strategy}


def skeleton_stop_allowed(memory: dict, events: list[dict], rounds: int, config: dict) -> bool:
    """A coarse readiness gate, not a claim that all real-world events were found."""
    strategies = {h.get("strategy") for h in memory["search_history"]}
    minimum_stages = config.get("exploration_memory", {}).get("minimum_stages", 3)
    return (rounds >= config["phase1_min_search_rounds"]
            and len(events) >= config["phase1_min_events"]
            and memory["skeleton_ready"]
            and not memory["next_search_directions"]
            and len(memory["stage_outline"]) >= minimum_stages
            and {"EARLIER", "LATER"}.issubset(strategies))
