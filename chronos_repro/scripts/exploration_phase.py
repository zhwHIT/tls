"""Document-driven skeleton exploration, using the same state in API and SFT."""
from __future__ import annotations

import copy
from chronos_repro.label_repair import BOUNDARY_MAX_CHARS, BOUNDARY_TARGET_CHARS

from chronos_repro.exploration_memory import (
    EXPLORATION_PROMPT, FEW_SHOTS, apply_memory_output,
    validate_exploration_policy, validate_memory_output, skeleton_stop_allowed,
    filter_invalid_memory_observations,
)
from chronos_repro.retrieval import search, fetch_documents
from chronos_repro.tisa_rollout import SKELETON
from chronos_repro.exploration_control import record_cycle_outcome


def memory_instruction(visible: dict) -> dict:
    return {
        "stage": "SKELETON_EXPLORATION: MEMORY_UPDATE from retrieved documents",
        "student_visible_state": visible,
        "output_limits": {"maximum_observations": 16, "maximum_keywords": 20,
                          "maximum_stages": 12, "maximum_search_directions": 8,
                          "boundary_assessment_max_chars": BOUNDARY_MAX_CHARS,
                          "boundary_assessment_target_chars": BOUNDARY_TARGET_CHARS},
        "objective": EXPLORATION_PROMPT + (
            " Extract important observed events and their dates from the supplied documents, "
            "including leads not yet admitted to the final timeline. Use literal evidence "
            "quotes and time expressions. Preserve YYYY or YYYY-MM precision; use null "
            "for unknown dates. Never substitute publication date for an unstated event date. "
            "Planned events must remain explicitly planned in summaries. Update the coarse "
            "stage outline from all remembered observations. Cite existing/current document IDs. "
            "List new actors or terms only if verbatim in these documents. Keep unverified "
            "hypotheses in next_search_directions, not observations. No detailed causal-gap "
            "reflection here. Assess earlier/later boundary probes using search history. "
            "Do not mark ready just because the current retrieval added nothing. "
            "Use deferred_directions=[] unless two completed comparable searches in history "
            "have both zero new_verified_event_count and zero evidence_update_count. "
            "List only actionable coarse leads as next_search_directions; do not refill all "
            "eight slots automatically. Set skeleton_ready=true when the coarse stages and "
            "boundary probes are sufficient and no actionable coarse lead remains, while "
            "retaining deferred uncertainties. Detailed unresolved gaps belong to phase two."
        ),
        "required_json": {
            "thought": "one brief evidence-grounded update reason",
            "action": "MEMORY_UPDATE",
            "observations": [{"summary": "provisional event supported by quote",
                              "event_time": "YYYY or YYYY-MM or YYYY-MM-DD, or null",
                              "time_expression": "verbatim dated expression in quote, or null",
                              "document_id": "current retrieval ID",
                              "evidence_quote": "literal quote from title/text"}],
            "discovered_keywords": ["literal actor or term in current documents"],
            "stage_outline": [{"period": "observed coarse period",
                               "description": "major stage supported by observations",
                               "evidence_ids": ["current or remembered document ID"]}],
            "next_search_directions": ["remaining coarse interval, boundary or facet to probe"],
            "deferred_directions": [{"direction": "uncertain coarse lead with no verified gain",
                                     "reason": "brief evidence-grounded reason; not a claim of completion",
                                     "attempted_queries": ["actual completed no-gain query 1", "actual completed no-gain query 2"]}],
            "boundary_assessment": "One short sentence about uncertain beginning/latest outcome; target <=320 characters, hard limit 600 including spaces. Do not enumerate all stages.",
            "skeleton_ready": False,
        },
    }


COARSE_PRIORITY = (
    ' First establish a broad chronological skeleton: origins, major intervening stages, '
    'and the latest evidenced outcome. Prefer an unprobed beginning/end or missing major '
    'stage over a single incident, casualty detail, pledge, or unresolved date. '
    'Pending leads are reminders for phase two, not a queue that phase one must exhaust. '
    'Use corpus publication-year counts only to guide where evidence may be available; '
    'they do not establish event dates or completeness. Do not repeatedly zoom into the '
    'same branch when other coarse stages remain unexplored. A skeleton may be ready '
    'with unresolved incident-level details; preserve those for GAP_REFINEMENT.'
)


def post_merge_memory_instruction(visible: dict) -> dict:
    instruction = memory_instruction(visible)
    instruction['stage'] = 'SKELETON_EXPLORATION: MEMORY_UPDATE post-merge outline refresh'
    instruction['objective'] = (
        'Update the coarse stage outline, directions and readiness AFTER verified events '
        'have been merged. Do not extract observations or keywords again: both arrays '
        'MUST be empty. Use the chronological verified events and actual cycle gains. '
        'stage_outline.evidence_ids may cite visible verified event IDs or exact stored '
        'passage IDs; the executor resolves event IDs to source provenance. '
        'No target-specific agenda or private reference. Only defer directions supported '
        'by two recorded no-gain probes. Assess readiness on the POST-MERGE state.'
    ) + COARSE_PRIORITY
    instruction['required_json']['observations'] = []
    instruction['required_json']['discovered_keywords'] = []
    instruction['required_json']['stage_outline'][0]['evidence_ids'] = ['visible verified event ID or stored passage ID']
    instruction['output_limits'].update(maximum_observations=0, maximum_keywords=0)
    return instruction


def policy_instruction(visible: dict) -> dict:
    return {"stage": "SKELETON_EXPLORATION: choose diversified SEARCH or autonomous STOP",
            "student_visible_state": visible, "objective": EXPLORATION_PROMPT + COARSE_PRIORITY,
            "few_shot_examples": FEW_SHOTS,
            "required_json": {"thought": "brief reason grounded in memory and current evidence",
                              "action": "SEARCH|STOP", "query": "3-20 English tokens; empty for STOP",
                              "strategy": "DISCOVER|EARLIER|LATER|INTERVAL|FACET|OVERVIEW|COMPLETE"}}


def run_exploration_phase(client, state, config, index, trajectory, usage):
    # Imported at execution time to keep reusable validation free of runner dependencies.
    from run_tisa_two_phase_annotation import (
        POLICY_SYSTEM, LabelValidationError, add_audits, execute_merge, model_step, phase1_visible,
        repaired_call, verify_batch,
    )

    state["exploration_memory"]["progress_control_enabled"] = config.get("exploration_memory", {}).get("progress_control_enabled", True)
    for round_index in range(config["phase1_max_rounds"] + 1):
        memory = state["exploration_memory"]
        stop_allowed = skeleton_stop_allowed(memory, state["timeline_events"], round_index, config)
        if round_index == config["phase1_max_rounds"]:
            visible = phase1_visible(state, ["STOP"])
            state["phase1_termination"] = "runner_limit"
            model_step(trajectory, SKELETON, "STOP", visible,
                       {"thought": "The exploration runner limit was reached; completeness is not established.",
                        "action": "STOP", "query": "", "strategy": "INTERRUPTED"},
                       {"terminal_for_phase": True, "forced": True, "skeleton_complete": False},
                       "deterministic_runner_limit_not_training_target")
            return
        visible = phase1_visible(state, ["SEARCH", "STOP"] if stop_allowed else ["SEARCH"])
        decision, audits = repaired_call(
            client, POLICY_SYSTEM, policy_instruction(visible),
            lambda output: validate_exploration_policy(output, visible), config,
        )
        add_audits(trajectory, usage, f"{SKELETON}:POLICY", audits)
        if decision["action"] == "STOP":
            state["phase1_termination"] = "autonomous_stop"
            model_step(trajectory, SKELETON, "STOP", visible, decision,
                       {"terminal_for_phase": True, "forced": False},
                       "deepseek_actual_rollout_no_private_reference")
            return

        query = decision["query"]
        results = search(index, [query], config["top_k"], f"{state['dataset']} {state['topic']}")
        documents = fetch_documents(index, state["topic"], [r["id"] for r in results], config["document_char_limit"])
        state["search_history"].append({"round": round_index + 1, "query": query,
                                        "strategy": decision["strategy"], "result_ids": [d["id"] for d in documents]})
        model_step(trajectory, SKELETON, "SEARCH", visible, decision, {"documents": documents},
                   "deepseek_actual_rollout_no_private_reference")

        memory_visible = phase1_visible(state, ["MEMORY_UPDATE"])
        memory_visible["tool_observation"] = {"query": query, "retrieved_documents": documents}
        rejected = []
        try:
            output, audits = repaired_call(
                client, POLICY_SYSTEM, memory_instruction(memory_visible),
                lambda payload: validate_memory_output(payload, memory, documents), config,
            )
        except LabelValidationError as error:
            output, rejected = filter_invalid_memory_observations(error.payload, memory, documents)
            audits = error.audits
        add_audits(trajectory, usage, f"{SKELETON}:MEMORY_UPDATE", audits)
        state["exploration_memory"] = apply_memory_output(memory, output, query, decision["strategy"], documents)
        model_step(trajectory, SKELETON, "MEMORY_UPDATE", memory_visible, output,
                   {"memory_after": copy.deepcopy(state["exploration_memory"]),
                    "validation_rejections": rejected,
                    "rejected_observations": [r for r in rejected if r.get("kind") == "observation"]},
                   ("deepseek_actual_rollout_filtered_after_validation" if rejected
                    else "deepseek_actual_rollout_no_private_reference"))

        verify_visible = phase1_visible(state, ["VERIFY"])
        state["valid_actions"] = ["VERIFY"]
        state["model_visible_state"] = verify_visible
        verified, audits = verify_batch(client, state, documents, config)
        add_audits(trajectory, usage, f"{SKELETON}:VERIFY", audits)
        candidates = [r for r in verified["candidates"]
                      if r["status"] in {"SUPPORTED", "CONFLICTED"}
                      and r["relevance_pass"] and r["contribution_pass"]]
        model_step(trajectory, SKELETON, "VERIFY",
                   {**verify_visible, "tool_observation": {"retrieved_documents": documents}}, verified,
                   {"passed_candidate_ids": [r["candidate_id"] for r in candidates]},
                   "deepseek_actual_rollout_no_private_reference")
        events_before_merge = copy.deepcopy(state["timeline_events"])
        execute_merge(client, state, candidates, config, trajectory, usage, SKELETON,
                      phase1_visible(state, ["MERGE"]))
        if state["exploration_memory"].get("progress_control_enabled"):
            state["exploration_memory"] = record_cycle_outcome(
                state["exploration_memory"], events_before_merge, state["timeline_events"])
            trajectory["steps"][-1]["observation"]["exploration_memory_after_merge"] = copy.deepcopy(state["exploration_memory"])
            trajectory["steps"][-1]["observation"]["events_after_merge"] = copy.deepcopy(state["timeline_events"])
