from __future__ import annotations

import copy
import re
from datetime import date
from typing import Iterable


SKELETON = 'SKELETON_EXPLORATION'
REFINE = 'GAP_REFINEMENT'
PHASES = (SKELETON, REFINE)
PHASE_ACTIONS = {
    SKELETON: ('SEARCH', 'SWITCH_PHASE'),
    REFINE: ('SEARCH', 'STOP'),
}
TOKEN = re.compile(r'[a-z0-9]+')


def _event_date(event: dict) -> str | None:
    value = event.get('time', {}).get('value')
    return str(value)[:10] if value else None


def _result_date(result: dict) -> str | None:
    value = result.get('timestamp') or result.get('date')
    return str(value)[:10] if value else None


def _tokens(text: str) -> set[str]:
    return set(TOKEN.findall(text.casefold()))


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a or b else 1.0


def gold_dates(private_target: dict) -> set[str]:
    events = private_target.get('gold_events')
    if isinstance(events, list):
        values = {
            str(value)[:10]
            for event in events
            for value in event.get('accepted_dates', [event.get('canonical_date')])
            if value
        }
        return values
    return {
        str(value)[:10]
        for value in private_target.get(
            'accepted_dates', [private_target.get('canonical_date')]
        )
        if value
    }


def gold_summaries(private_target: dict) -> list[str]:
    events = private_target.get('gold_events')
    if isinstance(events, list):
        return [str(event.get('summary', '')) for event in events]
    return [str(private_target.get('summary', ''))]


def build_coverage_memory(state: dict) -> dict:
    events = state.get('events', [])
    covered_dates = sorted({value for event in events if (value := _event_date(event))})
    return {
        'phase': SKELETON,
        'covered_dates': covered_dates,
        'anchor_event_ids': [
            str(event['event_id'])
            for event in events
            if event.get('event_id') and _event_date(event)
        ],
        'covered_facets': [],
        'uncovered_periods': [],
        'query_history': [],
        'failed_queries': [],
        'marginal_gain_history': [],
        'low_gain_streak': 0,
    }


def build_gap_memory(state: dict) -> dict:
    gaps = [
        {
            'gap_id': str(gap['gap_id']),
            'type': str(gap['type']),
            'priority': float(gap.get('priority', 0.0)),
            'status': 'OPEN',
            'window_start': gap.get('window_start'),
            'window_end': gap.get('window_end'),
        }
        for gap in state.get('gaps', [])
    ]
    return {
        'phase': REFINE,
        'open_gaps': gaps,
        'closed_gaps': [],
        'missing_elements': [],
        'conflicts': [
            str(event['event_id'])
            for event in state.get('events', [])
            if event.get('conflict')
        ],
        'attempted_queries': [],
        'evidence_support': {
            str(event['event_id']): int(event.get('support', 0))
            for event in state.get('events', [])
            if event.get('event_id')
        },
        'marginal_gain_history': [],
        'low_gain_streak': 0,
    }


def attach_phase_memory(state: dict, phase: str) -> dict:
    if phase not in PHASES:
        raise ValueError(f'Unsupported phase: {phase}')
    result = copy.deepcopy(state)
    result['phase'] = phase
    result['memory'] = (
        build_coverage_memory(result) if phase == SKELETON else build_gap_memory(result)
    )
    result['valid_actions'] = list(PHASE_ACTIONS[phase])
    return result


def validate_memory(memory: dict, phase: str) -> None:
    if not isinstance(memory, dict) or memory.get('phase') != phase:
        raise ValueError('Memory phase does not match policy phase')
    required = (
        {'covered_dates', 'anchor_event_ids', 'query_history', 'low_gain_streak'}
        if phase == SKELETON
        else {'open_gaps', 'closed_gaps', 'attempted_queries', 'low_gain_streak'}
    )
    missing = sorted(required - set(memory))
    if missing:
        raise ValueError(f'Memory is missing required fields: {missing}')
    if int(memory['low_gain_streak']) < 0:
        raise ValueError('low_gain_streak must be non-negative')


def validate_policy_state(state: dict) -> None:
    phase = str(state.get('phase', ''))
    if phase not in PHASES:
        raise ValueError(f'Invalid policy phase: {phase}')
    validate_memory(state.get('memory'), phase)
    allowed = set(PHASE_ACTIONS[phase])
    if set(state.get('valid_actions', [])) != allowed:
        raise ValueError('Policy valid_actions do not match phase')


def query_metrics(
    query: str,
    results: list[dict],
    private_target: dict,
    covered_dates: Iterable[str] = (),
    previous_queries: Iterable[str] = (),
) -> dict:
    target_dates = gold_dates(private_target)
    covered = {str(value)[:10] for value in covered_dates}
    result_dates = {value for row in results if (value := _result_date(row))}
    exact = len((result_dates & target_dates) - covered)
    window = 0
    for result_date in result_dates - covered:
        parsed = date.fromisoformat(result_date)
        if any(
            abs((parsed - date.fromisoformat(target)).days) <= 2
            for target in target_dates
        ):
            window += 1
    history = list(previous_queries)
    maximum_similarity = max(
        (_similarity(query, previous) for previous in history),
        default=0.0,
    )
    novelty = 1.0 - maximum_similarity
    new_dates = len(result_dates - covered)
    score = exact * 1000 + window * 100 + new_dates * 2 + len(results) + novelty
    return {
        'exact_gold_gain': exact,
        'window_2d_gold_gain': window,
        'new_date_count': new_dates,
        'new_document_count': len(results),
        'max_previous_query_similarity': maximum_similarity,
        'query_novelty': novelty,
        'score': score,
    }


def rank_query_candidates(
    candidates: list[str],
    results_by_query: dict[str, list[dict]],
    private_target: dict,
    covered_dates: Iterable[str] = (),
    previous_queries: Iterable[str] = (),
) -> list[dict]:
    rows = [
        {
            'query': query,
            'metrics': query_metrics(
                query,
                results_by_query.get(query, []),
                private_target,
                covered_dates,
                previous_queries,
            ),
        }
        for query in candidates
    ]
    return sorted(
        rows,
        key=lambda row: (
            -row['metrics']['score'],
            len(row['query']),
            row['query'].casefold(),
        ),
    )


def validate_query_candidates(candidates: object, minimum: int = 2, maximum: int = 4) -> list[str]:
    if not isinstance(candidates, list):
        raise ValueError('candidate_queries must be a list')
    queries = [str(query).strip() for query in candidates]
    if not minimum <= len(queries) <= maximum:
        raise ValueError(f'candidate_queries must contain {minimum} to {maximum} queries')
    if any(not 3 <= len(TOKEN.findall(query)) <= 20 for query in queries):
        raise ValueError('Every candidate query must contain 3 to 20 tokens')
    if len({query.casefold() for query in queries}) != len(queries):
        raise ValueError('candidate_queries must be unique')
    return queries


def validate_controller_review(
    review: dict,
    phase: str,
    candidates: list[str],
    ranking: list[dict],
    margin: float,
) -> None:
    if phase not in PHASES:
        raise ValueError(f'Unsupported phase: {phase}')
    chosen_action = str(review.get('chosen_action', ''))
    rejected_action = str(review.get('rejected_action', ''))
    allowed = set(PHASE_ACTIONS[phase])
    if chosen_action not in allowed or rejected_action not in allowed:
        raise ValueError('Controller action is invalid for phase')
    if chosen_action == rejected_action:
        raise ValueError('Chosen and rejected actions must differ')
    chosen_query = str(review.get('chosen_query', '')).strip()
    rejected_query = str(review.get('rejected_query', '')).strip()
    candidate_set = {query.casefold() for query in candidates}
    if chosen_action == 'SEARCH' and chosen_query.casefold() not in candidate_set:
        raise ValueError('Chosen SEARCH query is not a generated candidate')
    if rejected_query and rejected_query.casefold() not in candidate_set:
        raise ValueError('Rejected query is not a generated candidate')
    metrics = {row['query'].casefold(): row['metrics']['score'] for row in ranking}
    if chosen_query and rejected_query:
        if metrics[chosen_query.casefold()] < metrics[rejected_query.casefold()]:
            raise ValueError('Teacher chose a lower-scoring query')
    for name in ('query', 'action', 'memory'):
        score = review.get('preference_scores', {}).get(name)
        if not isinstance(score, dict):
            raise ValueError(f'Missing preference score: {name}')
        chosen, rejected = float(score['chosen']), float(score['rejected'])
        if not 0 <= rejected <= chosen <= 1 or chosen - rejected < margin:
            raise ValueError(f'Invalid preference margin: {name}')
    for name in ('memory_update_chosen', 'memory_update_rejected'):
        if not isinstance(review.get(name), dict):
            raise ValueError(f'{name} must be an object')
