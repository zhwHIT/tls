from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Helpers import this module by name. Share exception classes with the CLI entry.
if __name__ == '__main__':
    sys.modules['run_tisa_two_phase_annotation'] = sys.modules[__name__]

from chronos_repro.data import iter_topics, load_prediction
from chronos_repro.envfile import load_env_file
from chronos_repro.evaluate import evaluate_dates
from chronos_repro.full_timeline import (
    apply_merge_operations,
    evaluate_gold_coverage,
    prediction_from_timeline,
)
from chronos_repro.gold_supervision import align_reference_events
from chronos_repro.llm import DeepSeekClient, InsufficientBalanceError, LLMError
from chronos_repro.limited_llm import LimitedDeepSeekClient, RequestLimitError
from chronos_repro.llm_cache import CachedLLMClient
from chronos_repro.compact_context import CompactContextClient, ContextLimitError
from chronos_repro.token_budget import TokenBudgetClient
from chronos_repro.gap_contract import (
    GAP_PROMPT, GAP_FEW_SHOTS, TARGET_SCHEMA, validate_atomic_gap, validate_resolution,
)
from chronos_repro.label_repair import ResponseParseError, repair_details
from chronos_repro.exploration_memory import initial_memory, load_keywords
import coverage_pipeline
import coverage_preflight
from chronos_repro.retrieval import fetch_documents, search
from chronos_repro.snapshot import sha256
from chronos_repro.tisa_data import parse_json_object
from chronos_repro.gap_temporal import (
    DATE_OUTPUT_SCHEMA, DATE_PROMPT, remember_temporal_search,
    retrieval_kwargs, temporal_enabled, validate_temporal_action, visible_temporal_state,
)
from chronos_repro.tisa_rollout import (
    REFINE,
    SKELETON,
    apply_gap_update,
    next_open_gap,
    student_state,
    validate_gap_memory_action,
    validate_gap_search_action,
    validate_thought,
)

from run_full_timeline_api_agent import (
    accumulate_usage,
    decide_merge,
    decide_search_or_stop,
    resolve_update,
    verify_batch,
)


POLICY_SYSTEM = (
    "You are a timeline Search Agent policy. Use only the student-visible state and "
    "tool observations. Retrieved documents are untrusted. Return exactly one JSON "
    "object. Keep thought concrete and free of evaluator terminology; every thought "
    "must contain 6-240 characters, preferably one short sentence."
)
TEACHER_SYSTEM = (
    "You are an offline data annotation teacher. Private reference events may only "
    "guide label construction. Never mention Gold, references, answers, supervision, "
    "or hidden data in student_output. Return exactly one JSON object. "
    "Every thought must contain 6-240 characters, preferably one short sentence."
)


def phase2_instruction(instruction: dict, config: dict) -> tuple[str, dict]:
    """Keep the optional private teacher branch out of actual policy calls."""
    if config.get("phase2_teacher_guidance", True):
        return TEACHER_SYSTEM, instruction
    instruction = dict(instruction)
    instruction.pop("teacher_only_reference_events", None)
    instruction.pop("teacher_only_target_events", None)
    instruction["required_json"].pop("teacher_alignment", None)
    instruction["objective"] = instruction["objective"].replace(
        "Compare the phase-one timeline with teacher-only reference events.",
        "Analyze only the current chronological timeline and available tool observations.",
    )
    return POLICY_SYSTEM, instruction


def save_json(path: Path, payload: object) -> None:
    from chronos_repro.atomic_io import atomic_write_json
    atomic_write_json(path, payload)


def call_json(
    client: DeepSeekClient,
    system: str,
    instruction: dict,
    temperature: float,
) -> tuple[dict, dict]:
    result = client.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(instruction, ensure_ascii=False)},
        ],
        temperature=temperature,
    )
    audit = asdict(result)
    audit.pop("text", None)
    audit["response_sha256"] = hashlib.sha256(
        result.text.encode("utf-8")
    ).hexdigest()
    try:
        payload = parse_json_object(result.text)
    except (ValueError, TypeError) as error:
        raise ResponseParseError(str(error), result.text, audit) from error
    return payload, audit


class LabelValidationError(ValueError):
    def __init__(self, message, payload, audits):
        super().__init__(message)
        self.payload = payload
        self.audits = audits


def repaired_call(
    client: DeepSeekClient,
    system: str,
    instruction: dict,
    validator,
    config: dict,
) -> tuple[dict, list[dict]]:
    attempts = []
    current = dict(instruction)
    for attempt in range(config["label_repair_attempts"] + 1):
        audit = None
        payload = None
        try:
            payload, audit = call_json(
                client, system, current, config["temperature"]
            )
            checked = validator(payload)
            if hasattr(client, 'accept_last_response'):
                client.accept_last_response(checked)
            return checked, attempts + [audit]
        except (InsufficientBalanceError, ContextLimitError):
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            if isinstance(error, ResponseParseError):
                audit, payload = error.audit, error.response
            if audit is not None:
                attempts.append(audit)
            attempts.append({
                "attempt": attempt + 1,
                "validation_error": str(error),
            })
            if attempt >= config["label_repair_attempts"]:
                raise LabelValidationError(
                    f"label invalid after repair attempts: {error}", payload, attempts
                ) from error
            current['repair'] = repair_details(payload, error, attempt + 1)
    raise AssertionError("unreachable")


def add_audits(
    trajectory: dict,
    usage: dict,
    stage: str,
    audits: list[dict],
) -> None:
    trajectory["audits"].append({"stage": stage, "attempts": audits})
    for audit in audits:
        if "usage" in audit:
            accumulate_usage(usage, audit)


def model_step(
    trajectory: dict,
    phase: str,
    action: str,
    visible_input: dict,
    model_output: dict,
    observation: dict,
    label_source: str,
) -> None:
    print(f"[step {len(trajectory['steps']) + 1}] {phase} {action}", flush=True)
    trajectory["steps"].append({
        "step_id": f"step-{len(trajectory['steps']) + 1:03d}",
        "phase": phase,
        "action": action,
        "model_input": visible_input,
        "model_output": model_output,
        "observation": observation,
        "label_source": label_source,
    })


def coverage_memory(history: list[dict], events: list[dict]) -> dict:
    return {
        "phase": SKELETON,
        "query_history": list(history),
        "covered_dates": sorted({str(row["time"]) for row in events}),
        "event_count": len(events),
    }


def phase1_visible(state: dict, valid_actions: list[str]) -> dict:
    visible = student_state(
        state["dataset"],
        state["topic"],
        SKELETON,
        state["timeline_events"],
        (state["exploration_memory"] if "exploration_memory" in state
         else coverage_memory(state["search_history"], state["timeline_events"])),
        valid_actions,
    )
    if "exploration_memory" in state:
        visible["schema_version"] = 5
        visible["keywords"] = list(state.get("keywords", []))
    if state.get("task_description"):
        visible["task_description"] = state["task_description"]
    if state.get('corpus_profile'):
        visible['corpus_profile'] = state['corpus_profile']
    return visible


def execute_merge(
    client: DeepSeekClient,
    state: dict,
    candidates: list[dict],
    config: dict,
    trajectory: dict,
    usage: dict,
    phase: str,
    visible: dict,
) -> tuple[dict, list[dict]]:
    if not candidates:
        output = {
            "thought": "No verified candidate can improve the current timeline.",
            "operations": [],
        }
        model_step(
            trajectory, phase, "MERGE", visible, output,
            {"applied": [], "new_event_count": 0},
            "deterministic_no_candidates",
        )
        return output, []
    state["valid_actions"] = ["MERGE"]
    state["model_visible_state"] = visible
    merged, audits = decide_merge(client, state, candidates, config)
    add_audits(trajectory, usage, f"{phase}:MERGE", audits)
    by_candidate = {row["candidate_id"]: row for row in candidates}
    by_event = {row["event_id"]: row for row in state["timeline_events"]}
    resolved_updates = {}
    update_observations = []
    for operation in merged["operations"]:
        if operation["operation"] != "UPDATE":
            continue
        existing = by_event[operation["target_event_id"]]
        candidate = by_candidate[operation["candidate_id"]]
        fusion, fusion_audits = resolve_update(
            client, state, existing, candidate, config
        )
        add_audits(trajectory, usage, f"{phase}:UPDATE", fusion_audits)
        resolved_updates[operation["candidate_id"]] = fusion["merged_event"]
        update_observations.append(fusion)
    before = len(state["timeline_events"])
    state["timeline_events"], applied = apply_merge_operations(
        state["timeline_events"],
        candidates,
        merged["operations"],
        resolved_updates,
    )
    observation = {
        "applied": applied,
        "update_fusions": update_observations,
        "new_event_count": len(state["timeline_events"]) - before,
        "event_count": len(state["timeline_events"]),
    }
    if merged.get('executor_recovery'):
        observation.update(executor_recovery=merged['executor_recovery'], training_target=False)
    model_step(
        trajectory, phase, "MERGE",
        {**visible, "tool_observation": {"verified_candidates": candidates}},
        merged, observation,
        "executor_recovery_not_training_target" if merged.get('executor_recovery') else "deepseek_actual_rollout",
    )
    return merged, applied


def run_phase1(
    client: DeepSeekClient,
    state: dict,
    config: dict,
    index: Path,
    trajectory: dict,
    usage: dict,
) -> None:
    if config.get('batch_controller', {}).get('enabled'):
        from batched_search_phase import run_phase1 as run_batch_phase1
        return run_batch_phase1(client, state, config, index, trajectory, usage)
    if coverage_pipeline.enabled(config):
        return coverage_pipeline.run_phase1(client, state, config, index, trajectory, usage)
    if config.get("exploration_memory", {}).get("enabled", False):
        from exploration_phase import run_exploration_phase
        return run_exploration_phase(client, state, config, index, trajectory, usage)
    for round_index in range(config["phase1_max_rounds"] + 1):
        stop_allowed = (
            round_index >= config["phase1_min_search_rounds"]
            and len(state["timeline_events"]) >= config["phase1_min_events"]
        )
        force_stop = round_index == config["phase1_max_rounds"]
        valid = ["STOP"] if force_stop else (
            ["SEARCH", "STOP"] if stop_allowed else ["SEARCH"]
        )
        visible = phase1_visible(state, valid)
        state["model_visible_state"] = visible
        state["valid_actions"] = valid
        policy_config = dict(config)
        if force_stop:
            policy, audits = final_stop(
                client, visible, config, SKELETON,
                "The exploration pass reached its fixed runner limit."
            )
        else:
            policy, audits = decide_search_or_stop(
                client, state, policy_config, stop_allowed
            )
        add_audits(trajectory, usage, f"{SKELETON}:POLICY", audits)
        action = str(policy["action"]).upper()
        if action == "STOP":
            model_step(
                trajectory, SKELETON, "STOP", visible, policy,
                {"terminal_for_phase": True},
                "deepseek_actual_rollout_no_private_reference",
            )
            return

        query = str(policy["query"]).strip()
        results = search(
            index, [query], config["top_k"],
            f"{state['dataset']} {state['topic']}",
        )
        documents = fetch_documents(index, state["topic"], [row["id"] for row in results], config["document_char_limit"])
        state["search_history"].append({
            "round": round_index + 1,
            "query": query,
            "result_ids": [row["id"] for row in documents],
        })
        model_step(
            trajectory, SKELETON, "SEARCH", visible, policy,
            {"documents": documents},
            "deepseek_actual_rollout_no_private_reference",
        )
        verify_visible = phase1_visible(state, ["VERIFY"])
        state["valid_actions"] = ["VERIFY"]
        state["model_visible_state"] = verify_visible
        verified, audits = verify_batch(client, state, documents, config)
        add_audits(trajectory, usage, f"{SKELETON}:VERIFY", audits)
        candidates = [
            row for row in verified["candidates"]
            if row["status"] in {"SUPPORTED", "CONFLICTED"}
            and row["relevance_pass"] and row["contribution_pass"]
        ]
        model_step(
            trajectory, SKELETON, "VERIFY",
            {**verify_visible, "tool_observation": {
                "retrieved_documents": documents
            }},
            verified,
            {"passed_candidate_ids": [row["candidate_id"] for row in candidates]},
            "deepseek_actual_rollout_no_private_reference",
        )
        merge_visible = phase1_visible(state, ["MERGE"])
        execute_merge(
            client, state, candidates, config, trajectory, usage,
            SKELETON, merge_visible,
        )


def final_stop(
    client: DeepSeekClient,
    visible: dict,
    config: dict,
    phase: str,
    runner_note: str,
) -> tuple[dict, list[dict]]:
    instruction = {
        "stage": f"{phase}: STOP",
        "student_visible_state": visible,
        "runner_note": runner_note,
        "required_json": {
            "thought": "brief concrete stopping reason",
            "action": "STOP",
            "reason": "why further search is not required in this pass",
            "confidence": 0.0,
        },
    }

    def validator(payload: dict) -> dict:
        payload["thought"] = validate_thought(
            payload.get("thought") or payload.pop("reflection", None)
        )
        if str(payload.get("action", "")).upper() != "STOP":
            raise ValueError("final policy must emit STOP")
        confidence = float(payload["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("STOP confidence must be between zero and one")
        payload["action"] = "STOP"
        return payload

    return repaired_call(
        client, POLICY_SYSTEM, instruction, validator, config
    )


def initialize_gap_memory(
    client: DeepSeekClient,
    state: dict,
    private_events: list[dict],
    config: dict,
) -> tuple[dict, dict, list[dict]]:
    initial_gap_state = {"gaps": [], "history": []}
    if "exploration_memory" in state:
        initial_gap_state["exploration"] = copy.deepcopy(state["exploration_memory"])
    visible = student_state(
        state["dataset"], state["topic"], REFINE,
        state["timeline_events"], initial_gap_state,
        ["GAP_MEMORY"],
    )
    visible = visible_temporal_state(visible, config)
    instruction = {
        "stage": "GAP_REFINEMENT: initialize GAP_MEMORY",
        "student_visible_state": visible,
        "teacher_only_reference_events": private_events,
        "objective": (
            "Compare the phase-one timeline with teacher-only reference events. "
            "Create only important discontinuities, causal breaks, missing key "
            "events/factors, or evidence conflicts. Student-visible thought and gap "
            "descriptions must sound like direct analysis of the existing event "
            "sequence and must not mention how the gap was discovered."
        ),
        "limits": {"maximum_gaps": config["phase2_max_gaps"], "gap_description_max_chars": 180},
        "required_json": {
            "student_output": {
                "thought": "brief concrete analysis of the ordered sequence",
                "action": "GAP_MEMORY",
                "gaps": [{
                    "gap_id": "gap-001",
                    "type": (
                        "TEMPORAL_GAP|CAUSAL_GAP|MISSING_KEY_EVENT|"
                        "MISSING_FACTOR|EVIDENCE_CONFLICT"
                    ),
                    "description": "specific timeline problem without hidden-data terms",
                    "priority": 0.0,
                    "status": "OPEN",
                    "left_event_id": "existing event ID or null",
                    "right_event_id": "existing event ID or null",
                }],
            },
            "teacher_alignment": [{
                "gap_id": "gap-001",
                "target_event_ids": ["teacher-only event ID"],
            }],
        },
    }
    instruction['objective'] += ' ' + GAP_PROMPT
    instruction['few_shots'] = copy.deepcopy(GAP_FEW_SHOTS)
    instruction['required_json']['student_output']['gaps'][0]['retrieval_target'] = copy.deepcopy(TARGET_SCHEMA)
    allowed_targets = {row["event_id"] for row in private_events}

    def validator(payload: dict) -> dict:
        output = validate_gap_memory_action(
            payload["student_output"],
            state["timeline_events"],
            config["phase2_max_gaps"],
            require_atomic=config.get('atomic_gap_contract', True),
        )
        alignment = payload.get("teacher_alignment") if config.get("phase2_teacher_guidance", True) else []
        if not isinstance(alignment, list):
            raise ValueError("teacher_alignment must be a list")
        gap_ids = {row["gap_id"] for row in output["gaps"]}
        mapping = {}
        for row in alignment:
            gap_id = str(row["gap_id"])
            target_ids = list(dict.fromkeys(map(str, row["target_event_ids"])))
            if gap_id not in gap_ids:
                raise ValueError("teacher alignment refers to an unknown gap")
            if not set(target_ids).issubset(allowed_targets):
                raise ValueError("teacher alignment refers to an unknown target")
            mapping[gap_id] = target_ids
        return {
            "student_output": output,
            "teacher_alignment": mapping,
        }

    system, instruction = phase2_instruction(instruction, config)
    result, audits = repaired_call(
        client, system, instruction, validator, config
    )
    return visible, result, audits


def decide_gap_search(
    client: DeepSeekClient,
    visible: dict,
    private_targets: list[dict],
    config: dict,
) -> tuple[dict, list[dict]]:
    active_gap = visible["active_gap"]
    validate_atomic_gap(active_gap, visible['events'], required=config.get('atomic_gap_contract', True))
    instruction = {
        "stage": "GAP_REFINEMENT: analyze one gap then SEARCH",
        "student_visible_state": visible,
        "teacher_only_target_events": private_targets,
        "objective": (
            "Write one brief concrete thought about the active temporal or causal "
            "problem, then emit SEARCH. Use a focused English query when evidence is "
            "needed. Use query as the empty string when this gap needs no search; "
            "the runner will advance to the next gap."
        ),
        "required_json": {
            "student_output": {
                "thought": "brief reason tied to the active gap",
                "action": "SEARCH",
                "gap_id": active_gap["gap_id"],
                "query": "3-20 token English query, or empty string",
            }
        },
    }

    instruction['objective'] += (
        ' Answer the active retrieval_target.question only. Its completion_criterion '
        'defines success; seed_query is a starting suggestion, not a mandatory query. '
        'Use this gap search history to avoid repeating failed query/range combinations. '
        'Do not broaden the task to the whole topic. Empty query means skip, not resolved.'
    )
    if temporal_enabled(config):
        instruction["objective"] += " " + DATE_PROMPT
        instruction["required_json"]["student_output"]["date_filter"] = DATE_OUTPUT_SCHEMA

    def validator(payload: dict) -> dict:
        raw = payload["student_output"]
        checked = validate_gap_search_action(raw, active_gap)
        if temporal_enabled(config):
            checked["date_filter"] = raw.get("date_filter")
        return validate_temporal_action(checked, visible, config)

    system, instruction = phase2_instruction(instruction, config)
    return repaired_call(
        client, system, instruction, validator, config
    )


def refresh_gap_memory(
    client: DeepSeekClient,
    visible: dict,
    active_gap: dict,
    cycle_observation: dict,
    private_targets: list[dict],
    config: dict,
) -> tuple[dict, list[dict]]:
    instruction = {
        "stage": "GAP_REFINEMENT: update GAP_MEMORY after MERGE",
        "student_visible_state": visible,
        "active_gap": active_gap,
        "cycle_observation": cycle_observation,
        "teacher_only_target_events": private_targets,
        "objective": (
            "Mark the active gap resolved only if verified merged events close it. "
            "Record any new discontinuity exposed by the updated ordered timeline. "
            "Do not restate existing gaps and do not mention hidden supervision."
        ),
        "limits": {"maximum_new_gaps": 2, "gap_description_max_chars": 180},
        "required_json": {
            "student_output": {
                "thought": "brief concrete post-merge assessment",
                "action": "GAP_MEMORY",
                "resolved_gap_ids": [active_gap["gap_id"]],
                "gaps": [{
                    "gap_id": "new unique gap ID",
                    "type": (
                        "TEMPORAL_GAP|CAUSAL_GAP|MISSING_KEY_EVENT|"
                        "MISSING_FACTOR|EVIDENCE_CONFLICT"
                    ),
                    "description": "newly exposed problem",
                    "priority": 0.0,
                    "status": "OPEN",
                    "left_event_id": "existing event ID or null",
                    "right_event_id": "existing event ID or null",
                }],
            }
        },
    }
    instruction['objective'] += ' ' + GAP_PROMPT + (
        ' Compare merged evidence against the active completion_criterion. '
        'Resolving requires resolution_evidence citing a current non-conflicted event '
        'and an exact quote from its summary. A quote alone is not enough: it must '
        'answer the stated question. Resolve only the active gap. If unresolved, '
        'use empty resolved_gap_ids and resolution_evidence. New gaps must be linked '
        'to an active anchor or a newly appended/updated event; do not restart global exploration.'
    )
    instruction['required_json']['student_output']['gaps'][0]['retrieval_target'] = copy.deepcopy(TARGET_SCHEMA)
    instruction['required_json']['student_output']['resolution_evidence'] = [
        {'gap_id': active_gap['gap_id'], 'event_id': 'current supported event ID',
         'quote': 'verbatim evidence from that event summary; [] if unresolved'}]
    known_ids = {
        row["gap_id"] for row in visible["memory"].get("gaps", [])
    }

    def validator(payload: dict) -> dict:
        raw = payload["student_output"]
        checked = validate_gap_memory_action(
            {
                "thought": raw.get("thought"),
                "action": raw.get("action"),
                "gaps": raw.get("gaps", []),
            },
            visible["events"],
            2,
            require_atomic=config.get('atomic_gap_contract', True),
        )
        resolved = list(dict.fromkeys(map(str, raw.get("resolved_gap_ids", []))))
        if not set(resolved).issubset(known_ids):
            raise ValueError("GAP_MEMORY resolves an unknown gap")
        if any(row["gap_id"] in known_ids for row in checked["gaps"]):
            raise ValueError("new gap duplicates an existing gap ID")
        proofs = validate_resolution(raw, active_gap, visible['events'],
                                     required=config.get('atomic_gap_contract', True))
        if config.get('atomic_gap_contract', True):
            local_ids = {active_gap.get('left_event_id'), active_gap.get('right_event_id')} - {None}
            local_ids.update(row.get('event_id') for row in cycle_observation.get('applied', [])
                             if row.get('applied_operation') in {'APPEND', 'UPDATE'})
            for gap in checked['gaps']:
                if not ({gap.get('left_event_id'), gap.get('right_event_id')} - {None}) & local_ids:
                    raise ValueError('new gap must be linked to the active anchors or changed events')
        return {
            "thought": checked["thought"],
            "action": "GAP_MEMORY",
            "resolved_gap_ids": resolved,
            "gaps": checked["gaps"],
            "resolution_evidence": proofs,
        }

    system, instruction = phase2_instruction(instruction, config)
    return repaired_call(
        client, system, instruction, validator, config
    )


def run_phase2(
    client: DeepSeekClient,
    state: dict,
    private_events: list[dict],
    config: dict,
    index: Path,
    trajectory: dict,
    usage: dict,
) -> dict:
    if config.get('batch_controller', {}).get('enabled'):
        from batched_search_phase import run_phase2 as run_batch_phase2
        return run_batch_phase2(client, state, config, index, trajectory, usage)
    maximum_attempts = config.get('phase2_max_attempts_per_gap', 3)
    if type(maximum_attempts) is not int or maximum_attempts < 1:
        raise ValueError('phase2_max_attempts_per_gap must be a positive integer')
    visible, initialized, audits = initialize_gap_memory(
        client, state, private_events, config
    )
    add_audits(trajectory, usage, f"{REFINE}:GAP_MEMORY_INIT", audits)
    output = initialized["student_output"]
    gap_memory = {"phase": REFINE, "gaps": output["gaps"], "history": []}
    if "exploration_memory" in state:
        gap_memory["exploration"] = copy.deepcopy(state["exploration_memory"])
    label_source = ("deepseek_teacher_private_reference_distilled" if config.get("phase2_teacher_guidance", True)
                    else "deepseek_actual_rollout_no_private_reference")
    alignment = dict(initialized["teacher_alignment"])
    model_step(
        trajectory, REFINE, "GAP_MEMORY", visible, output,
        {"gap_count": len(output["gaps"])},
        label_source,
    )

    for cycle in range(config["phase2_max_gap_cycles"]):
        active_gap = next_open_gap(gap_memory)
        if active_gap is None:
            break
        visible = student_state(
            state["dataset"], state["topic"], REFINE,
            state["timeline_events"], gap_memory, ["SEARCH"], active_gap,
        )
        visible = visible_temporal_state(visible, config)
        gap_memory = copy.deepcopy(visible["memory"])
        active_gap = visible["active_gap"]
        target_ids = alignment.get(active_gap["gap_id"], [])
        targets = [
            row for row in private_events if row["event_id"] in target_ids
        ]
        state['gap_memory'] = gap_memory
        decision, audits = decide_gap_search(
            client, visible, targets, config
        )
        add_audits(
            trajectory, usage, f"{REFINE}:GAP_SEARCH_DECISION", audits
        )
        query = decision["query"]
        if not query:
            remember_temporal_search(gap_memory, active_gap["gap_id"], decision, [], config)
            model_step(
                trajectory, REFINE, "SEARCH", visible, decision,
                {"skipped": True, "reason": "empty query means no search needed"},
                label_source,
            )
            verify_output = {
                "thought": "No documents were requested for this gap.",
                "candidates": [],
                "skipped": True,
            }
            model_step(
                trajectory, REFINE, "VERIFY",
                student_state(
                    state["dataset"], state["topic"], REFINE,
                    state["timeline_events"], gap_memory, ["VERIFY"], active_gap,
                ),
                verify_output, {"passed_candidate_ids": []},
                "deterministic_empty_query_transition",
            )
            merge_output = {
                "thought": "No candidate exists, so the timeline remains unchanged.",
                "operations": [],
                "skipped": True,
            }
            model_step(
                trajectory, REFINE, "MERGE",
                student_state(
                    state["dataset"], state["topic"], REFINE,
                    state["timeline_events"], gap_memory, ["MERGE"], active_gap,
                ),
                merge_output, {"applied": [], "new_event_count": 0},
                "deterministic_empty_query_transition",
            )
            gap_memory, _ = apply_gap_update(
                gap_memory, active_gap["gap_id"], "", [], [],
                state["timeline_events"], config["phase2_max_gaps"],
            )
            state['gap_memory'] = gap_memory
            if coverage_pipeline.enabled(config):
                coverage_pipeline.checkpoint(state, trajectory, usage)
            continue

        parameters = retrieval_kwargs(decision, config)
        results = search(
            index, [query], config["top_k"],
            f"{state['dataset']} {state['topic']}", **parameters,
        )
        remember_temporal_search(gap_memory, active_gap["gap_id"], decision, results, config)
        documents = (coverage_pipeline.retrieve_passages(state, config, index, query, results, parameters, passage_limit=4)
                     if coverage_pipeline.enabled(config) else fetch_documents(
                         index, state["topic"], [row["id"] for row in results], config["document_char_limit"]))
        state["search_history"].append({
            "phase": REFINE,
            "gap_id": active_gap["gap_id"],
            "query": query,
            "retrieval_parameters": parameters,
            "result_ids": [row["id"] for row in documents],
        })
        model_step(
            trajectory, REFINE, "SEARCH", visible, decision,
            {"documents": documents, "retrieval_parameters": parameters, "results": results},
            label_source,
        )

        state["valid_actions"] = ["VERIFY"]
        verify_visible = student_state(
            state["dataset"], state["topic"], REFINE,
            state["timeline_events"], gap_memory, ["VERIFY"], active_gap,
        )
        state["model_visible_state"] = verify_visible
        if coverage_pipeline.enabled(config):
            candidates, merged, applied = coverage_pipeline.process_passages(
                client, state, documents, config, trajectory, usage, REFINE, verify_visible)
            verified, audits = {"candidates": candidates}, []
        else:
            verified, audits = verify_batch(client, state, documents, config)
        add_audits(trajectory, usage, f"{REFINE}:VERIFY", audits)
        candidates = [
            row for row in verified["candidates"]
            if row["status"] in {"SUPPORTED", "CONFLICTED"}
            and row["relevance_pass"] and row["contribution_pass"]
        ]
        if not coverage_pipeline.enabled(config):
            model_step(
                trajectory, REFINE, "VERIFY",
                {**verify_visible, "tool_observation": {"retrieved_documents": documents}},
                verified,
                {"passed_candidate_ids": [row["candidate_id"] for row in candidates]},
                "deepseek_actual_rollout_evidence_only",
            )

        merge_visible = student_state(
            state["dataset"], state["topic"], REFINE,
            state["timeline_events"], gap_memory, ["MERGE"], active_gap,
        )
        if not coverage_pipeline.enabled(config):
            merged, applied = execute_merge(
                client, state, candidates, config, trajectory, usage,
                REFINE, merge_visible,
            )
        cycle_observation = {
            "query": query,
            "retrieval_parameters": parameters,
            "verified_candidates": candidates,
            "all_extracted_candidates": verified["candidates"],
            "merge_operations": merged["operations"],
            "applied": applied,
        }
        if coverage_pipeline.enabled(config):
            coverage_pipeline.refresh_evidence_memory(state)
            gap_memory["exploration"] = copy.deepcopy(state.get("exploration_memory", {}))
        refresh_visible = student_state(
            state["dataset"], state["topic"], REFINE,
            state["timeline_events"], gap_memory, ["GAP_MEMORY"], active_gap,
        )
        refresh_visible = visible_temporal_state(refresh_visible, config)
        refreshed, audits = refresh_gap_memory(
            client, refresh_visible, active_gap, cycle_observation,
            targets, config,
        )
        add_audits(
            trajectory, usage, f"{REFINE}:GAP_MEMORY_UPDATE", audits
        )
        gap_memory, gap_audit = apply_gap_update(
            gap_memory,
            active_gap["gap_id"],
            query,
            refreshed["resolved_gap_ids"],
            refreshed["gaps"],
            state["timeline_events"],
            config["phase2_max_gaps"],
            maximum_attempts=config.get('phase2_max_attempts_per_gap', 3),
        )
        state['gap_memory'] = gap_memory
        for new_gap_id in gap_audit["new_gap_ids"]:
            alignment.setdefault(new_gap_id, target_ids)
        current = next(
            row for row in gap_memory["gaps"]
            if row["gap_id"] == active_gap["gap_id"]
        )
        if current["status"] == "IN_PROGRESS":
            current["status"] = "OPEN"
        model_step(
            trajectory, REFINE, "GAP_MEMORY",
            {**refresh_visible, "tool_observation": cycle_observation},
            refreshed,
            gap_audit,
            label_source,
        )

        if coverage_pipeline.enabled(config):
            coverage_pipeline.checkpoint(state, trajectory, usage)

    state['gap_memory'] = gap_memory
    stop_visible = student_state(
        state["dataset"], state["topic"], REFINE,
        state["timeline_events"], gap_memory, ["STOP"],
    )
    stop_visible = visible_temporal_state(stop_visible, config)
    gap_memory = stop_visible["memory"]
    unresolved = sum(row["status"] in {"OPEN", "IN_PROGRESS", "FAILED", "DEFERRED"} for row in gap_memory["gaps"])
    if unresolved:
        model_step(
            trajectory, REFINE, "STOP", stop_visible,
            {"thought": "The runner stopped with unresolved or deferred gaps; completion is not established.",
             "action": "STOP", "query": ""},
            {"terminal": True, "forced": True, "open_gap_count": unresolved},
            "deterministic_runner_limit_not_training_target",
        )
        return {"memory": gap_memory, "teacher_alignment": alignment}
    stop, audits = final_stop(
        client, stop_visible, config, REFINE,
        "All selected gaps were processed or the fixed runner cycle limit was reached.",
    )
    add_audits(trajectory, usage, f"{REFINE}:STOP", audits)
    model_step(
        trajectory, REFINE, "STOP", stop_visible, stop,
        {"terminal": True, "open_gap_count": sum(
            row["status"] == "OPEN" for row in gap_memory["gaps"]
        )},
        "deepseek_actual_rollout",
    )
    return {
        "memory": gap_memory,
        "teacher_alignment": alignment,
    }


def restore_failed_phase1(
    path: Path,
    config: dict,
) -> tuple[dict, dict, dict] | None:
    if not path.exists() or not config.get("resume_failed_phase1", True):
        return None
    previous = json.loads(path.read_text(encoding="utf-8"))
    steps = previous.get("steps", [])
    if (
        previous.get("schema_version") != 4
        or previous.get("status") != "stopped_error"
        or not steps
        or steps[-1].get("phase") != SKELETON
        or steps[-1].get("action") not in {"VERIFY", "STOP"}
    ):
        return None
    history = []
    for step in steps:
        if step.get("phase") != SKELETON or step.get("action") != "SEARCH":
            continue
        documents = step.get("observation", {}).get("documents", [])
        history.append({
            "round": len(history) + 1,
            "query": str(step.get("model_output", {}).get("query", "")),
            "result_ids": [str(row["id"]) for row in documents],
        })
    state = {
        "dataset": config["dataset"],
        "topic": config["topic"],
        "timeline_events": previous.get("final_events", []),
        "gaps": ["resume the pending verified candidate batch"],
        "search_history": history,
        "valid_actions": ["MERGE"],
    }
    previous.pop("error", None)
    previous["status"] = "running"
    usage = previous.get("usage") or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "logical_calls": 0,
        "http_attempts": 0,
    }
    return state, previous, usage


def finish_resumed_phase1(
    client: DeepSeekClient,
    state: dict,
    config: dict,
    trajectory: dict,
    usage: dict,
) -> None:
    verified = trajectory["steps"][-1]["model_output"]
    candidates = [
        row for row in verified["candidates"]
        if row["status"] in {"SUPPORTED", "CONFLICTED"}
        and row["relevance_pass"] and row["contribution_pass"]
    ]
    visible = phase1_visible(state, ["MERGE"])
    execute_merge(
        client, state, candidates, config, trajectory, usage,
        SKELETON, visible,
    )
    stop_visible = phase1_visible(state, ["STOP"])
    stop, audits = final_stop(
        client, stop_visible, config, SKELETON,
        "The resumed exploration pass completed its pending merge.",
    )
    add_audits(trajectory, usage, f"{SKELETON}:STOP", audits)
    model_step(
        trajectory, SKELETON, "STOP", stop_visible, stop,
        {"terminal_for_phase": True, "resumed": True},
        "deepseek_actual_rollout_no_private_reference",
    )


def assert_no_budget_fields(value: object) -> None:
    forbidden = {"budget", "query_budget", "queries_left", "rounds_left"}
    if isinstance(value, dict):
        leaked = forbidden & set(map(str, value))
        if leaked:
            raise ValueError(
                f"SFT model input leaks runtime budget fields: {sorted(leaked)}"
            )
        for nested in value.values():
            assert_no_budget_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            assert_no_budget_fields(nested)


def compile_sft_rows(trajectory: dict) -> list[dict]:
    rows = []
    for step in trajectory["steps"]:
        if not step["label_source"].startswith("deepseek"):
            continue
        assert_no_budget_fields(step["model_input"])
        rows.append({
            "schema_version": step["model_input"].get("schema_version", 4),
            "trajectory_id": trajectory["trajectory_id"],
            "step_id": step["step_id"],
            "tools": [
                "SEARCH", "MEMORY_UPDATE", "VERIFY", "MERGE", "GAP_MEMORY", "STOP"
            ],
            "messages": [
                {"role": "system", "content": POLICY_SYSTEM + (" " + DATE_PROMPT if "retrieval_contract" in step["model_input"] else "")},
                {
                    "role": "user",
                    "content": json.dumps(
                        step["model_input"], ensure_ascii=False, sort_keys=True
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        step["model_output"], ensure_ascii=False, sort_keys=True
                    ),
                },
            ],
            "metadata": {
                "dataset": trajectory["dataset"],
                "topic": trajectory["topic"],
                "split": trajectory.get('split', 'unassigned'),
                "training_ready": False,
                "semantic_review_required": True,
                "phase": step["phase"],
                "action": step["action"],
                "label_source": step["label_source"],
                "contains_runtime_budget": False,
                "events_are_chronologically_sorted": True,
            },
        })
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Local coverage preflight only; never loads the API key")
    parser.add_argument("--allow-api", action="store_true", help="Use only after explicit approval of the run envelope")
    parser.add_argument('--replay-from-start', action='store_true',
                        help='Explicitly acknowledge replay, not checkpoint continuation; retain cumulative ledger')
    args = parser.parse_args()

    project = Path(args.project_root).resolve()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if coverage_pipeline.enabled(config) and config.get("phase2_teacher_guidance", False):
        raise ValueError("Coverage evaluation must not expose private references to policy")
    if coverage_pipeline.enabled(config) and not (args.dry_run or args.allow_api):
        parser.error('Coverage API is disabled: use --dry-run, or --allow-api after approval')
    data_root = project / config["data"]
    index = project / config["index"]
    output = Path(args.output_dir).resolve()
    if not output.is_relative_to(project):
        raise ValueError('Run output must remain inside project')
    from chronos_repro.run_guard import exclusive_run
    with exclusive_run(output):
        return execute_locked(args, project, config_path, config, output)


def execute_locked(args, project, config_path, config, output):
    data_root = project / config['data']
    index = project / config['index']
    run_config_path = output / "run_config.json"
    if run_config_path.exists() and json.loads(run_config_path.read_text(encoding="utf-8")) != config:
        raise ValueError("Output directory belongs to a different configuration; use a new output directory")
    execution = None
    if coverage_pipeline.enabled(config) and not args.dry_run:
        from chronos_repro.run_guard import prepare_execution
        execution = prepare_execution(project, output, args.replay_from_start)
    save_json(run_config_path, config)
    continuation = None
    if config.get('continuation') and not args.dry_run:
        from batch_continuation import prepare
        continuation = prepare(project, output, config)
    if coverage_pipeline.enabled(config) or args.dry_run:
        report = coverage_preflight.build_preflight(project, config_path, config, args.env_file)
        if continuation is not None:
            for key, value in continuation['input_binding'].items():
                if key != 'config_sha256' and report['binding'].get(key) != value:
                    raise ValueError('Continuation source input changed: ' + key)
        binding_path = output / 'run_binding.json'
        if binding_path.exists() and json.loads(binding_path.read_text(encoding='utf-8')) != report['binding']:
            raise ValueError('Run input binding changed; do not reuse this run directory')
        save_json(binding_path, report['binding'])
        save_json(output / 'preflight.json', report)
        if args.dry_run:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
    load_env_file(args.env_file)

    topic = next(
        row for row in iter_topics(data_root)
        if row.topic_id == config["topic"]
    )
    private_events = [
        {
            "event_id": row["event_id"],
            "canonical_date": row["canonical_date"],
            "accepted_dates": row["accepted_dates"],
            "summary": row["summary"],
        }
        for row in align_reference_events(topic.topic_id, topic.timelines)
    ]
    client = (coverage_preflight.guarded_coverage_client(config, output, allow_replay_from_start=args.replay_from_start) if coverage_pipeline.enabled(config) else
              LimitedDeepSeekClient(model=config["model"], max_retries=4, retry_backoff_seconds=5.0,
                                   request_limit=config["max_api_requests"])
              if config.get("max_api_requests") else DeepSeekClient(model=config["model"], max_retries=4, retry_backoff_seconds=5.0))
    raw_client = client
    if temporal_enabled(config):
        client = CachedLLMClient(client, output / "api_cache")
    token_client = None
    if config.get('batch_controller', {}).get('enabled'):
        token_settings = copy.deepcopy(config['batch_controller']['tokens'])
        token_settings['tokenizer_path'] = str(project / token_settings['tokenizer_path'])
        token_client = TokenBudgetClient(client, token_settings)
        client = token_client
    state = {
        "dataset": config["dataset"],
        "topic": config["topic"],
        "timeline_events": [],
        "gaps": [
            "broad chronology",
            "major milestones",
            "beginning and ending boundaries",
        ],
        "search_history": [],
        "valid_actions": ["SEARCH"],
    }
    if coverage_pipeline.enabled(config):
        state["_output_dir"] = str(output)
        state['_frozen_topic_path'] = str(data_root / config['topic'] / 'articles.preprocessed.jsonl.gz')
        import sqlite3
        from chronos_repro.retrieval import _bm25_path
        with sqlite3.connect(_bm25_path(index).resolve().as_uri() + '?mode=ro', uri=True) as connection:
            years = connection.execute('SELECT substr(timestamp,1,4),count(*) FROM documents WHERE topic=? GROUP BY substr(timestamp,1,4)', [config['topic']]).fetchall()
        state['corpus_profile'] = {'publication_year_counts': dict(years),
            'semantics': 'Corpus availability, not event boundaries. Prefer evidence-led queries; explore other periods only when retrieved leads support them.'}
    schema_version = 5 if temporal_enabled(config) else 4
    if config.get('compact_context', {}).get('enabled', False):
        if config.get('phase2_teacher_guidance', True):
            raise ValueError('Compact evaluation requires phase2_teacher_guidance=false')
        client = CompactContextClient(client, config['compact_context'])
    if config.get("exploration_memory", {}).get("enabled", False):
        state["keywords"] = load_keywords(data_root / config["topic"])
        state["exploration_memory"] = initial_memory(state["keywords"])
    if config.get("task_description"):
        state["task_description"] = config["task_description"]
    trajectory = {
        "schema_version": schema_version,
        "trajectory_id": (
            f"{config['dataset']}:{config['topic']}:"
            f"two-phase-deepseek-rollout-v{schema_version}"
        ),
        "dataset": config["dataset"],
        "topic": config["topic"],
        "model": config["model"],
        "annotation_definition": {
            "phase1": "actual DeepSeek SEARCH/VERIFY/MERGE/STOP without private reference",
            "phase2": (
                "private-reference-guided GAP_MEMORY and SEARCH decisions; "
                "evidence-only VERIFY/MERGE; student outputs hide supervision"
            ) if config.get("phase2_teacher_guidance", True) else (
                "policy-only GAP_MEMORY/SEARCH with visible temporal anchors; evidence-only VERIFY/MERGE"
            ),
            "temporal_search": config.get("temporal_search", {}),
            "runtime_budget_visible_to_model": False,
            "events_order": "chronological",
            "empty_gap_query": "no search needed; advance to next gap",
        },
        "initial_state": phase1_visible(state, ["SEARCH"]),
        "steps": [],
        "audits": [],
    }
    usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "logical_calls": 0,
        "http_attempts": 0,
    }
    if coverage_pipeline.enabled(config):
        trajectory["trajectory_id"] += "-coverage-v6-" + sha256(config_path)[:8]
        trajectory["pipeline_revision"] = config.get('pipeline_revision', 'coverage-v6')
        trajectory['execution'] = execution
        trajectory['source_sha256'] = execution['source_sha256']
    restored = None if coverage_pipeline.enabled(config) else restore_failed_phase1(output / "trajectory.json", config)
    if restored is not None:
        state, trajectory, usage = restored
    if continuation is not None:
        from batch_continuation import restore
        restore(state, trajectory, continuation, config, index)
        continuation = None  # Old audit history remains on disk, outside live memory.
    status = "running"
    phase2_private = {}
    try:
        if config.get('continuation'):
            from batch_continuation import finish_pending
            finish_pending(client, state, config, trajectory, usage)
        if restored is None:
            if not state.get('phase1_termination'):
                run_phase1(client, state, config, index, trajectory, usage)
        elif trajectory["steps"][-1]["action"] == "VERIFY":
            finish_resumed_phase1(client, state, config, trajectory, usage)
        phase2_private = run_phase2(
            client, state, private_events, config, index, trajectory, usage
        )
        if coverage_pipeline.enabled(config):
            coverage_pipeline.finalize(client, state, config, trajectory, usage)
        status = "ok"
    except InsufficientBalanceError as error:
        status = "stopped_insufficient_balance"
        trajectory["error"] = str(error)
    except RequestLimitError as error:
        status = "stopped_request_limit"
        trajectory["error"] = str(error)
    except LabelValidationError as error:
        status = 'stopped_error'
        trajectory['error'] = str(error)
        add_audits(trajectory, usage, 'FAILED_LABEL_VALIDATION', error.audits)
    except (LLMError, ValueError, KeyError, TypeError) as error:
        status = "stopped_error"
        trajectory["error"] = str(error)

    trajectory["status"] = status
    trajectory["usage"] = usage
    if isinstance(raw_client, LimitedDeepSeekClient):
        trajectory['http_requests_started'] = raw_client.http_requests_started
        trajectory['http_requests_total'] = raw_client.http_requests_total
    if isinstance(client, (CachedLLMClient, CompactContextClient)):
        trajectory["api_cache"] = client.statistics()
    trajectory["final_events"] = state["timeline_events"]
    if config.get('batch_controller', {}).get('enabled'):
        trajectory['controller_memory'] = state.get('controller_memory', {})
        trajectory['query_batches'] = state.get('query_batches', [])
        trajectory['phase2_termination'] = state.get('phase2_termination')
        save_json(output / 'token_usage_audit.json', token_client.statistics())
    trajectory['verification_quarantine'] = state.get('verification_quarantine', [])
    if "exploration_memory" in state:
        trajectory["final_exploration_memory"] = state["exploration_memory"]
        trajectory["phase1_termination"] = state.get("phase1_termination")
    if not phase2_private and state.get('gap_memory'):
        phase2_private = {'memory': state['gap_memory']}
    if phase2_private:
        trajectory["final_gap_memory"] = phase2_private["memory"]
    prediction = prediction_from_timeline(state["timeline_events"])
    if coverage_pipeline.enabled(config):
        trajectory["evidence_progress"] = state.get("evidence_progress", {})
        trajectory["incomplete_extraction"] = state.get("incomplete_extraction", [])
        save_json(output / "candidate_pool.json", state.get("candidate_pool", []))
        pool = state.get("event_pool", state["timeline_events"])
        save_json(output / "event_pool.json", pool)
        save_json(output / "event_pool_prediction.json", prediction_from_timeline(pool))
        if "_evidence_reader" in state:
            save_json(output / "evidence_reader_manifest.json", state["_evidence_reader"].manifest())
    save_json(output / "trajectory.json", trajectory)
    save_json(output / "prediction.json", prediction)

    parsed_prediction = load_prediction(output / "prediction.json")
    coverage = evaluate_gold_coverage(
        config["topic"], prediction, topic.timelines,
        config["semantic_coverage_threshold"],
    )
    evaluation = {
        "date_score": asdict(evaluate_dates(parsed_prediction, topic.timelines)),
        "coverage": coverage,
    }
    if coverage_pipeline.enabled(config):
        pool_prediction = load_prediction(output / "event_pool_prediction.json")
        evaluation["event_pool_date_score"] = asdict(evaluate_dates(pool_prediction, topic.timelines))
        evaluation["coverage_acceptance"] = {
            "target_gold_date_recall": 0.7,
            "minimum_date_hits": __import__('math').ceil(0.7 * coverage['gold_date_count']),
            "majority_dates_reached": coverage["gold_date_recall"] >= 0.7,
            "semantic_event_coverage": "not established by lexical proxy; independent review required",
            "gold_used_only_after_rollout": True,
            "runtime_status": status,
            "forced_stop_count": sum(bool(s.get('observation', {}).get('forced')) for s in trajectory['steps'] if s['action'] == 'STOP'),
            "incomplete_extraction_batches": len(state.get('incomplete_extraction', [])),
            "unresolved_lead_count": state.get('exploration_memory', {}).get('unresolved_lead_count', 0),
        }
    save_json(output / "evaluation.json", evaluation)

    sft_rows = compile_sft_rows(trajectory)
    if token_client is not None:
        sft_rows = []
        for row in token_client.accepted:
            if row['metadata']['is_repair']:
                continue
            row['metadata'].update(dataset=state['dataset'], topic=state['topic'],
                                   split=config.get('evaluation_role', 'unassigned'),
                                   label_source='deepseek_batch_search_policy',
                                   stop_means='retrieval_not_worthwhile_not_complete')
            sft_rows.append(row)
        write_jsonl(output / 'batch_controller.review_required.jsonl', sft_rows)
    if isinstance(client, CompactContextClient):
        write_jsonl(output / 'controller_exact.review_required.jsonl', client.accepted)
        save_json(output / 'context_projections.json', client.projections)
    write_jsonl(output / f"sft_v{schema_version}.jsonl", sft_rows)
    if coverage_pipeline.enabled(config):
        controller = [r for r in sft_rows if r['metadata']['action'] in {'SEARCH', 'STOP', 'MEMORY_UPDATE', 'GAP_MEMORY'}]
        write_jsonl(output / 'controller_candidates.review_required.jsonl', controller)
        save_json(output / 'training_gate.json', {'training_ready': False, 'runtime_status': status,
            'controller_candidate_count': len(controller), 'reason': 'Exact inference prompt alignment and semantic review remain required; do not train directly on raw audit exports.'})
    private_sidecar = {
        "schema_version": schema_version,
        "trajectory_id": trajectory["trajectory_id"],
        "teacher_alignment": phase2_private.get("teacher_alignment", {}),
        "private_event_ids": [row["event_id"] for row in private_events],
        "must_not_enter_student_training_input": True,
    }
    save_json(output / "teacher_alignment.private.json", private_sidecar)
    manifest = {
        "schema_version": schema_version,
        "status": status,
        "model": config["model"],
        "dataset": config["dataset"],
        "topic": config["topic"],
        "usage": usage,
        "counts": {
            "steps": len(trajectory["steps"]),
            "sft_rows": len(sft_rows),
            "events": len(state["timeline_events"]),
            "private_reference_events": len(private_events),
        },
        "coverage": coverage,
        "privacy_checks": {
            "runtime_budget_in_sft_input": False,
            "private_reference_in_sft_input": False,
            "teacher_alignment_is_separate": True,
        },
        "inputs": {
            "config_sha256": sha256(config_path),
            "index_sha256": sha256(index),
        },
        "outputs": {
            name: sha256(output / name)
            for name in (
                "trajectory.json",
                "prediction.json",
                "evaluation.json",
                f"sft_v{schema_version}.jsonl",
                "teacher_alignment.private.json",
            )
        },
    }
    if isinstance(client, (CachedLLMClient, CompactContextClient)):
        manifest["api_cache"] = client.statistics()
    if isinstance(raw_client, LimitedDeepSeekClient):
        manifest['http_requests_started'] = raw_client.http_requests_started
        manifest['http_requests_total'] = raw_client.http_requests_total
    if coverage_pipeline.enabled(config):
        manifest["pipeline_revision"] = config.get('pipeline_revision', 'coverage-v6')
        manifest["evidence_progress"] = state.get("evidence_progress", {})
        for name in ("candidate_pool.json", "event_pool.json", "event_pool_prediction.json", "evidence_reader_manifest.json"):
            if (output / name).exists():
                manifest["outputs"][name] = sha256(output / name)
    save_json(output / "manifest.json", manifest)
    if status == "stopped_insufficient_balance":
        return 3
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
