"""Offline memory-transition audit and descriptive comparison; no model calls."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from chronos_repro.exploration_memory import validate_memory_output, apply_memory_output
from chronos_repro.tisa_rollout import SKELETON, REFINE
from chronos_repro.exploration_control import record_cycle_outcome


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize(folder):
    trace = read(folder / "trajectory.json")
    phase2 = next((s for s in trace["steps"] if s["phase"] == REFINE), None)
    skeleton = phase2["model_input"]["events"] if phase2 else trace.get("final_events", [])
    rounds, seen = [], set()
    for step in trace["steps"]:
        if step["phase"] != SKELETON or step["action"] != "SEARCH":
            continue
        docs = step["observation"].get("documents", [])
        ids = {str(d["id"]) for d in docs}
        dates = sorted({str(d["publication_date"]) for d in docs if d.get("publication_date")})
        rounds.append({"step_id": step["step_id"], "query": step["model_output"]["query"],
                       "strategy": step["model_output"].get("strategy"),
                       "returned": len(docs), "new_documents": len(ids - seen),
                       "publication_dates": dates})
        seen.update(ids)
    memory = trace.get("final_exploration_memory", {})
    event_dates = sorted({e["time"] for e in skeleton})
    return {"status": trace["status"], "phase1_termination": trace.get("phase1_termination"),
            "steps": len(trace["steps"]), "action_counts": dict(Counter(s["action"] for s in trace["steps"])),
            "phase1_searches": len(rounds), "phase1_unique_documents": len(seen),
            "phase1_event_count": len(skeleton), "phase1_event_dates": event_dates,
            "phase1_event_year_counts": dict(Counter(e["time"][:4] for e in skeleton)),
            "final_event_count": len(trace.get("final_events", [])),
            "rounds": rounds, "memory_observations": len(memory.get("observed_events", [])),
            "memory_unknown_dates": sum(o["event_time"] is None for o in memory.get("observed_events", [])),
            "skeleton_ready": memory.get("skeleton_ready"),
            "stage_outline": memory.get("stage_outline", []),
            "remaining_directions": memory.get("next_search_directions", []),
            "gap_status_counts": dict(Counter(g["status"] for g in trace.get("final_gap_memory", {}).get("gaps", []))),
            "evaluation": read(folder / "evaluation.json"), "api_cache": trace.get("api_cache")}


def audit_memory(trace):
    failures, checks, last_search = [], 0, None
    outcome_checks = 0
    for step in trace["steps"]:
        if step["phase"] != SKELETON:
            continue
        if step["action"] == "SEARCH":
            last_search = step
        if step["action"] == "MERGE" and "exploration_memory_after_merge" in step.get("observation", {}):
            try:
                actual = record_cycle_outcome(step["model_input"]["memory"], step["model_input"]["events"],
                                              step["observation"]["events_after_merge"])
                if actual != step["observation"]["exploration_memory_after_merge"]:
                    raise ValueError("post-MERGE progress transition does not reproduce recorded state")
                outcome_checks += 1
            except (ValueError, KeyError, TypeError) as exc:
                failures.append({"step_id": step["step_id"], "error": str(exc)})
        if step["action"] != "MEMORY_UPDATE":
            continue
        try:
            visible = step["model_input"]
            docs = visible["tool_observation"]["retrieved_documents"]
            checked = validate_memory_output(step["model_output"], visible["memory"], docs)
            if last_search is None:
                raise ValueError("memory update has no preceding SEARCH")
            after = apply_memory_output(visible["memory"], checked,
                                        last_search["model_output"]["query"],
                                        last_search["model_output"]["strategy"], docs)
            if after != step["observation"]["memory_after"]:
                raise ValueError("memory transition does not reproduce recorded state")
            checks += 1
        except (ValueError, KeyError, TypeError) as exc:
            failures.append({"step_id": step["step_id"], "error": str(exc)})
    phase2 = next((s for s in trace["steps"] if s["phase"] == REFINE), None)
    inherited = (phase2 is not None and phase2["model_input"]["memory"].get("exploration")
                 == trace.get("final_exploration_memory"))
    return {"memory_transitions_checked": checks, "post_merge_progress_checked": outcome_checks, "memory_transitions_valid": not failures and checks > 0,
            "phase2_inherits_memory": inherited, "failures": failures}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    folder = Path(args.run_dir)
    trace = read(folder / "trajectory.json")
    report = {"schema": "chronos-repro.exploration-comparison.v1", "current": summarize(folder),
              "baseline": summarize(Path(args.baseline_dir)), "audit": audit_memory(trace),
              "limitations": ["The search budget differs; this comparison cannot isolate the effect of memory.",
                              "Publication-date breadth does not establish event coverage or semantic correctness.",
                              "Coarse stages and readiness are model assessments, not independent ground truth."]}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"audit": report["audit"], "phase1_searches": report["current"]["phase1_searches"],
                      "phase1_event_count": report["current"]["phase1_event_count"],
                      "phase1_termination": report["current"]["phase1_termination"]}, ensure_ascii=False))
    if not report["audit"]["memory_transitions_valid"] or not report["audit"]["phase2_inherits_memory"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
