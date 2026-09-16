from __future__ import annotations

from collections import defaultdict


def extract_queries(payload: dict, source: dict) -> list[str]:
    source_type = str(source["type"])
    if source_type == "top_level_queries":
        values = payload.get(str(source.get("field", "queries")), [])
    elif source_type == "baseline_queries":
        baseline = payload.get("baselines", {}).get(str(source["name"]), {})
        values = baseline.get("queries", [])
    elif source_type == "trajectory_steps":
        values = []
        for step in payload.get("steps", []):
            action = step.get("action")
            action_name = action.get("type") if isinstance(action, dict) else action
            if str(action_name).upper() != "SEARCH":
                continue
            query = None
            for container_name in ("arguments", "model_output"):
                container = step.get(container_name)
                if isinstance(container, dict) and container.get("query") is not None:
                    query = container["query"]
                    break
            if query is None and isinstance(action, dict):
                query = action.get("query")
            values.append(query)
    else:
        raise ValueError(f"Unsupported query source type: {source_type}")
    queries = [str(value).strip() for value in values if str(value or "").strip()]
    if not queries:
        raise ValueError(f"No non-empty SEARCH queries found for source {source_type}")
    return queries


def query_document_ids(case_report: dict) -> list[str]:
    output = []
    seen = set()
    for run in case_report.get("runs", []):
        if run.get("status") != "complete":
            continue
        for round_row in run["evaluation"]["rounds"]:
            for value in round_row["ranked_document_ids"]:
                document_id = str(value)
                if document_id not in seen:
                    seen.add(document_id)
                    output.append(document_id)
    return output


def missing_event_ids_by_document(
    event_ids: list[str],
    document_ids: list[str],
    qrels: list[dict],
) -> dict[str, list[str]]:
    existing = {
        (str(row["event_id"]), str(row["document_id"]))
        for row in qrels
    }
    return {
        document_id: [
            event_id
            for event_id in event_ids
            if (event_id, document_id) not in existing
        ]
        for document_id in document_ids
        if any(
            (event_id, document_id) not in existing
            for event_id in event_ids
        )
    }


def evaluate_query_sequence(
    queries: list[str],
    retrieved_rounds: list[list[dict]],
    qrels: list[dict],
) -> dict:
    if len(queries) != len(retrieved_rounds):
        raise ValueError("queries and retrieved_rounds must have equal length")
    if not queries:
        raise ValueError("query sequence must not be empty")

    grades: dict[tuple[str, str], int] = {}
    judged_documents: set[str] = set()
    for row in qrels:
        event_id = str(row["event_id"])
        document_id = str(row["document_id"])
        grades[(event_id, document_id)] = int(row["grade"])
        judged_documents.add(document_id)

    direct_pairs = {pair for pair, grade in grades.items() if grade >= 2}
    contextual_pairs = {pair for pair, grade in grades.items() if grade >= 1}
    resolved_events = {event_id for event_id, _ in direct_pairs}
    direct_by_document: dict[str, set[tuple[str, str]]] = defaultdict(set)
    contextual_by_document: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for pair in direct_pairs:
        direct_by_document[pair[1]].add(pair)
    for pair in contextual_pairs:
        contextual_by_document[pair[1]].add(pair)

    seen_documents: list[str] = []
    seen_set: set[str] = set()
    recovered_direct: set[tuple[str, str]] = set()
    recovered_contextual: set[tuple[str, str]] = set()
    covered_events: set[str] = set()
    rounds = []

    for round_index, (query, documents) in enumerate(
        zip(queries, retrieved_rounds), start=1
    ):
        ranked_ids = [str(row["id"]) for row in documents]
        new_documents = []
        for document_id in ranked_ids:
            if document_id not in seen_set:
                seen_set.add(document_id)
                seen_documents.append(document_id)
                new_documents.append(document_id)

        direct_before = set(recovered_direct)
        contextual_before = set(recovered_contextual)
        covered_before = set(covered_events)
        for document_id in new_documents:
            recovered_direct.update(direct_by_document.get(document_id, set()))
            recovered_contextual.update(
                contextual_by_document.get(document_id, set())
            )
        covered_events = {event_id for event_id, _ in recovered_direct}
        rounds.append(
            {
                "round": round_index,
                "query": query,
                "returned_documents": len(ranked_ids),
                "new_documents": len(new_documents),
                "new_judged_documents": sum(
                    document_id in judged_documents for document_id in new_documents
                ),
                "marginal_direct_pairs": len(recovered_direct - direct_before),
                "marginal_contextual_pairs": len(
                    recovered_contextual - contextual_before
                ),
                "newly_covered_events": sorted(covered_events - covered_before),
                "cumulative_covered_events": len(covered_events),
                "cumulative_event_coverage": (
                    len(covered_events) / len(resolved_events)
                    if resolved_events
                    else 0.0
                ),
                "ranked_document_ids": ranked_ids,
            }
        )

    return {
        "queries": len(queries),
        "resolved_events": len(resolved_events),
        "covered_events": len(covered_events),
        "event_coverage": (
            len(covered_events) / len(resolved_events) if resolved_events else 0.0
        ),
        "available_direct_pairs": len(direct_pairs),
        "recovered_direct_pairs": len(recovered_direct),
        "direct_pair_recall": (
            len(recovered_direct) / len(direct_pairs) if direct_pairs else 0.0
        ),
        "available_contextual_or_direct_pairs": len(contextual_pairs),
        "recovered_contextual_or_direct_pairs": len(recovered_contextual),
        "contextual_or_direct_pair_recall": (
            len(recovered_contextual) / len(contextual_pairs)
            if contextual_pairs
            else 0.0
        ),
        "unique_retrieved_documents": len(seen_set),
        "judged_retrieved_documents": len(seen_set & judged_documents),
        "unjudged_retrieved_documents": len(seen_set - judged_documents),
        "rounds": rounds,
    }
