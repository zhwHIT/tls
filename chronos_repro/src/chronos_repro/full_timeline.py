from __future__ import annotations

from datetime import date
import copy
import re

from .date_evidence import validate_date_evidence

from .gold_supervision import align_reference_events, text_similarity


MERGE_OPERATIONS = {"APPEND", "UPDATE", "DROP"}


class DuplicateAppendError(ValueError):
    def __init__(self, candidate_id, existing):
        self.repair_context = {
            'candidate_id': candidate_id, 'matching_event_id': existing['event_id'],
            'existing_event': {k: existing[k] for k in ('event_id', 'time', 'summary')},
            'instruction': 'This APPEND repeats an existing event. Use UPDATE with the supplied '
                           'event ID for new evidence, or DROP if there is no contribution.'}
        super().__init__(f"APPEND duplicates an existing event: candidate {candidate_id}, "
                         f"target {existing['event_id']}; choose UPDATE or DROP")


def validate_verified_candidates(
    candidates: list[dict], documents: list[dict], maximum: int, *, strict_dates: bool = True
) -> list[dict]:
    if not isinstance(candidates, list) or len(candidates) > maximum:
        raise ValueError("VERIFY candidates must be a bounded list")
    available = {str(row["id"]) for row in documents}
    seen = set()
    output = []
    for row in candidates:
        candidate_id = str(row["candidate_id"])
        if not candidate_id or candidate_id in seen:
            raise ValueError("VERIFY candidate IDs must be unique and non-empty")
        seen.add(candidate_id)
        status = str(row["status"]).upper()
        if status not in {"SUPPORTED", "CONFLICTED", "INSUFFICIENT", "IRRELEVANT"}:
            raise ValueError(f"Unknown VERIFY status: {status}")
        event = row["event"]
        event_date = event.get("time")
        if not strict_dates or status in {"SUPPORTED", "CONFLICTED"}:
            date.fromisoformat(str(event_date))
            if len(str(event_date)) != 10:
                raise ValueError("Formal events require day precision supported by evidence")
        elif event_date is not None:
            from .exploration_memory import _period
            _period(event_date)
        if len(str(event["summary"]).split()) < 4:
            raise ValueError("Candidate summary is too short")
        evidence_ids = list(dict.fromkeys(map(str, row.get("evidence_ids", []))))
        missing = sorted(set(evidence_ids) - available)
        if missing:
            raise ValueError(f"VERIFY cites unavailable evidence IDs: {missing}")
        confidence = float(row["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("Candidate confidence must be between zero and one")
        if status in {"SUPPORTED", "CONFLICTED"} and not evidence_ids:
            raise ValueError("Supported candidate requires evidence")
        grounded_date = None
        if strict_dates and event_date is not None:
            try:
                grounded_date = validate_date_evidence(event_date, row.get("date_evidence"), documents, evidence_ids)
            except ValueError as error:
                raise ValueError(f"VERIFY candidate {candidate_id}: {error}") from error
        output.append({
            **row,
            "candidate_id": candidate_id,
            "status": status,
            "event": {
                "time": event_date,
                "summary": str(event["summary"]).strip(),
                "actors": list(event.get("actors") or []),
                "location": event.get("location"),
            },
            "evidence_ids": evidence_ids,
            "confidence": confidence,
            **({"date_evidence": grounded_date} if strict_dates else {}),
        })
    return output


def validate_merge_operations(
    operations: list[dict], candidates: list[dict], timeline: list[dict], *, exact_duplicate_guard: bool = False
) -> list[dict]:
    if not isinstance(operations, list):
        raise ValueError("MERGE operations must be a list")
    candidate_ids = {row["candidate_id"] for row in candidates}
    timeline_ids = {row["event_id"] for row in timeline}
    seen = set()
    update_targets = set()
    output = []
    for row in operations:
        candidate_id = str(row["candidate_id"])
        operation = str(row["operation"]).upper()
        if candidate_id not in candidate_ids or candidate_id in seen:
            raise ValueError(f"Invalid or duplicate MERGE candidate: {candidate_id}")
        if operation not in MERGE_OPERATIONS:
            raise ValueError(f"Unknown MERGE operation: {operation}")
        target = row.get("target_event_id")
        if operation == "UPDATE" and str(target) not in timeline_ids:
            raise ValueError(f"UPDATE target does not exist: {target}")
        candidate = next(item for item in candidates if item["candidate_id"] == candidate_id)
        matching = _matching_existing(timeline, candidate, exact_only=exact_duplicate_guard) if operation == 'APPEND' else None
        if operation == "APPEND" and matching is not None:
            raise DuplicateAppendError(candidate_id, matching)
        if operation == "UPDATE":
            if str(target) in update_targets:
                raise ValueError("Only one UPDATE per existing event is allowed in a batch")
            update_targets.add(str(target))
        seen.add(candidate_id)
        output.append({
            "candidate_id": candidate_id,
            "operation": operation,
            "target_event_id": str(target) if target is not None else None,
            "reason": str(row.get("reason", "")).strip(),
        })
    supported = {
        row["candidate_id"] for row in candidates
        if row["status"] in {"SUPPORTED", "CONFLICTED"}
    }
    if not supported.issubset(seen):
        raise ValueError("MERGE must decide every supported/conflicted candidate")
    return output


def recover_exact_duplicate_appends(operations, candidates, timeline):
    """Omit only literal duplicate APPENDs after failed model repair; keep other validation strict."""
    if not isinstance(operations, list):
        raise ValueError('MERGE operations must be a list')
    corrected = copy.deepcopy(operations)
    by_id = {r['candidate_id']: r for r in candidates}
    changes = []
    for row in corrected:
        candidate = by_id.get(row.get('candidate_id'))
        if row.get('operation', '').upper() != 'APPEND' or candidate is None:
            continue
        existing = _matching_existing(timeline, candidate, exact_only=True)
        if existing is None:
            continue
        changes.append({'candidate_id': candidate['candidate_id'], 'existing_event_id': existing['event_id'],
                        'original_operation': copy.deepcopy(row),
                        'candidate_retained_for_review': copy.deepcopy(candidate)})
        row.update(operation='DROP', target_event_id=None,
                   reason='Executor omits exact repeated event after failed repair; candidate and evidence retained for review.')
    if not changes:
        raise ValueError('No exact duplicate APPEND to recover')
    checked = validate_merge_operations(corrected, candidates, timeline, exact_duplicate_guard=True)
    return checked, {'kind': 'exact_duplicate_append_omitted', 'changes': changes,
                     'new_evidence_fused': False, 'training_target': False}


def _matching_existing(timeline: list[dict], candidate: dict, *, exact_only: bool = False) -> dict | None:
    event = candidate["event"]
    normalize = lambda s: re.sub(r"\s+", " ", s).strip().casefold()
    matches = [
        row for row in timeline
        if row["time"] == event["time"]
        and (normalize(row["summary"]) == normalize(event["summary"]) if exact_only
             else text_similarity(row["summary"], event["summary"]) >= 0.35)
    ]
    return max(matches, key=lambda row: text_similarity(row["summary"], event["summary"]), default=None)


def validate_resolved_update(existing: dict, candidate: dict, merged: dict) -> dict:
    if str(merged.get("event_id")) != str(existing["event_id"]):
        raise ValueError("UPDATE must preserve the existing event_id")
    event_date = str(merged["time"])
    date.fromisoformat(event_date)
    if event_date not in {str(existing["time"]), str(candidate["event"]["time"])}:
        raise ValueError("UPDATE date must come from one of the two input events")
    summary = str(merged["summary"]).strip()
    if len(summary.split()) < 4:
        raise ValueError("Merged summary is too short")
    expected_evidence = set(map(str, existing.get("evidence_ids", []))) | set(candidate["evidence_ids"])
    actual_evidence = set(map(str, merged.get("evidence_ids", [])))
    if actual_evidence != expected_evidence:
        raise ValueError("UPDATE must preserve the exact evidence union")
    confidence = float(merged["confidence"])
    if not 0 <= confidence <= 1:
        raise ValueError("UPDATE confidence must be between zero and one")
    actors = merged.get("actors")
    if not isinstance(actors, list):
        raise ValueError("UPDATE actors must be a list")
    if not isinstance(merged.get("conflict"), bool):
        raise ValueError("UPDATE conflict must be Boolean")
    return {
        "event_id": str(existing["event_id"]),
        "time": event_date,
        "summary": summary,
        "actors": list(map(str, actors)),
        "location": merged.get("location"),
        "evidence_ids": sorted(actual_evidence),
        "confidence": confidence,
        "conflict": bool(merged["conflict"]),
        **({"date_evidence": copy.deepcopy(candidate["date_evidence"])}
           if event_date == str(candidate["event"]["time"]) and candidate.get("date_evidence")
           else {"date_evidence": copy.deepcopy(existing["date_evidence"])}
           if event_date == str(existing["time"]) and existing.get("date_evidence") else {}),
    }


def apply_merge_operations(
    timeline: list[dict], candidates: list[dict], operations: list[dict],
    resolved_updates: dict[str, dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    updated = [dict(row) for row in timeline]
    by_candidate = {row["candidate_id"]: row for row in candidates}
    audit = []
    next_number = len(updated) + 1
    resolved_updates = resolved_updates or {}
    for decision in operations:
        candidate = by_candidate[decision["candidate_id"]]
        operation = decision["operation"]
        if candidate["status"] not in {"SUPPORTED", "CONFLICTED"}:
            operation = "DROP"
        if operation == "APPEND":
            event = candidate["event"]
            new_row = {
                "event_id": f"event-{next_number:03d}",
                "time": event["time"],
                "summary": event["summary"],
                "actors": event["actors"],
                "location": event["location"],
                "evidence_ids": candidate["evidence_ids"],
                "confidence": candidate["confidence"],
                "conflict": candidate["status"] == "CONFLICTED",
                **({"date_evidence": copy.deepcopy(candidate["date_evidence"])} if candidate.get("date_evidence") else {}),
            }
            updated.append(new_row)
            next_number += 1
            audit.append({**decision, "applied_operation": "APPEND", "event_id": new_row["event_id"]})
        elif operation == "UPDATE":
            target_id = decision.get("target_event_id")
            target = next((row for row in updated if row["event_id"] == target_id), None)
            if target is None:
                raise ValueError("Cannot apply UPDATE without matching target")
            if decision["candidate_id"] not in resolved_updates:
                raise ValueError("UPDATE requires a separately validated model merge result")
            target.clear()
            target.update(resolved_updates[decision["candidate_id"]])
            audit.append({**decision, "applied_operation": operation, "event_id": target["event_id"]})
        else:
            audit.append({**decision, "applied_operation": "DROP"})
    updated.sort(key=lambda row: (row["time"], row["event_id"]))
    return updated, audit


def prediction_from_timeline(timeline: list[dict]) -> list[list[object]]:
    by_date: dict[str, list[str]] = {}
    for event in sorted(timeline, key=lambda row: (row["time"], row["event_id"])):
        by_date.setdefault(event["time"], []).append(event["summary"])
    return [[event_date, summaries] for event_date, summaries in sorted(by_date.items())]


def evaluate_gold_coverage(
    topic_id: str,
    prediction: list[list[object]],
    references: tuple[dict[date, tuple[str, ...]], ...],
    similarity_threshold: float = 0.2,
) -> dict:
    predicted = [
        {"date": str(event_date), "summary": summary}
        for event_date, summaries in prediction
        for summary in summaries
    ]
    gold = align_reference_events(topic_id, references)
    matched_gold = []
    for event in gold:
        best = max(
            (
                text_similarity(event["summary"], row["summary"])
                for row in predicted
                if row["date"] in event["accepted_dates"]
            ),
            default=0.0,
        )
        if best >= similarity_threshold:
            matched_gold.append(event["event_id"])
    gold_dates = {d.isoformat() for reference in references for d in reference}
    predicted_dates = {row["date"] for row in predicted}
    covered_dates = gold_dates & predicted_dates
    return {
        "gold_event_count": len(gold),
        "event_metric_kind": "lexical_jaccard_diagnostic_not_semantic_recall",
        "lexical_event_match_recall": len(matched_gold) / len(gold) if gold else 1.0,
        "semantic_event_coverage_established": False,
        "matched_gold_events": len(matched_gold),
        "gold_event_recall": len(matched_gold) / len(gold) if gold else 1.0,
        "gold_date_count": len(gold_dates),
        "covered_gold_dates": len(covered_dates),
        "gold_date_recall": len(covered_dates) / len(gold_dates) if gold_dates else 1.0,
        "extra_date_count": len(predicted_dates - gold_dates),
        "matched_gold_event_ids": matched_gold,
        "missing_gold_dates": sorted(gold_dates - predicted_dates),
    }
