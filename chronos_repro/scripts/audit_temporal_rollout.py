"""Replay v5 retrieval locally and audit the exported trajectory and SFT."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from chronos_repro.gap_temporal import retrieval_kwargs, validate_temporal_action
from chronos_repro.retrieval import search
from chronos_repro.temporal_filter import in_publication_range
from chronos_repro.tisa_rollout import REFINE, validate_student_state


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    folder = Path(args.run_dir)
    trace, config = read(folder / "trajectory.json"), read(args.config)
    checks = {"completed": trace["status"] == "ok", "student_states_valid": True,
              "temporal_actions_valid": True, "retrieval_replays_identical": True,
              "hard_dates_in_range": True, "sft_matches_recorded_steps": True,
              "final_evidence_ids_previously_retrieved": True}
    searches, failures, seen = [], [], set()
    for step in trace["steps"]:
        try:
            validate_student_state(step["model_input"])
        except ValueError as exc:
            checks["student_states_valid"] = False
            failures.append(str(exc))
        if step["action"] != "SEARCH":
            continue
        output = step["model_output"]
        query = output["query"]
        kwargs = {}
        if step["phase"] == REFINE:
            try:
                validate_temporal_action(output, step["model_input"], config)
            except ValueError as exc:
                checks["temporal_actions_valid"] = False
                failures.append(str(exc))
            kwargs = retrieval_kwargs(output, config)
        expected = [str(d["id"]) for d in step["observation"].get("documents", [])]
        actual = search(config["index"], [query], config["top_k"], f"{trace['dataset']} {trace['topic']}", **kwargs) if query else []
        ids = [str(d["id"]) for d in actual]
        matches = ids == expected
        checks["retrieval_replays_identical"] &= matches
        if kwargs.get("date_filter_mode") == "hard":
            checks["hard_dates_in_range"] &= all(in_publication_range(d["timestamp"], kwargs["date_from"], kwargs["date_to"]) for d in actual)
        searches.append({"step_id": step["step_id"], "phase": step["phase"], "query": query,
                         "thought": output.get("thought"), "date_filter": output.get("date_filter"),
                         "returned": len(ids), "new_document_count": len(set(ids) - seen),
                         "replay_identical": matches})
        seen.update(ids)
        print(f"replayed {step['step_id']}: {matches}", flush=True)
    sft = [json.loads(line) for line in (folder / "sft_v5.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_step = {s["step_id"]: s for s in trace["steps"]}
    for row in sft:
        step = by_step[row["step_id"]]
        checks["sft_matches_recorded_steps"] &= json.loads(row["messages"][1]["content"]) == step["model_input"] and json.loads(row["messages"][2]["content"]) == step["model_output"]
    evidence = {str(i) for e in trace["final_events"] for i in e.get("evidence_ids", [])}
    checks["final_evidence_ids_previously_retrieved"] = evidence.issubset(seen)
    phase1 = next((s["model_input"]["events"] for s in trace["steps"] if s["phase"] == REFINE), [])
    report = {"schema": "chronos-repro.temporal-rollout-audit.v1", "valid": all(checks.values()),
              "checks": checks, "failures": failures,
              "counts": {"steps": len(trace["steps"]), "sft_rows": len(sft), "phase1_events": len(phase1),
                         "final_events": len(trace["final_events"]), "unique_retrieved_documents": len(seen)},
              "action_counts": dict(Counter(s["action"] for s in trace["steps"])),
              "searches": searches, "gap_status_counts": dict(Counter(g["status"] for g in trace.get("final_gap_memory", {}).get("gaps", []))),
              "validation_errors": [a for stage in trace["audits"] for a in stage["attempts"] if "validation_error" in a],
              "stop_steps": [s for s in trace["steps"] if s["action"] == "STOP"],
              "evaluation": read(folder / "evaluation.json"),
              "limitations": ["Evidence ID membership does not prove semantic faithfulness.", "Loop-limit termination is not autonomous completion."]}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"valid": report["valid"], "counts": report["counts"], "gap_status_counts": report["gap_status_counts"]}))
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
