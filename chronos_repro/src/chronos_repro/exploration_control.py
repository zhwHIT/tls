"""Observable search progress and conservative lexical intent safeguards."""
from __future__ import annotations

import copy
import re


def similar_intent(left: str, right: str) -> bool:
    """Lexical proxy, not an embedding/semantic equivalence oracle."""
    def tokens(text):
        return set(re.findall(r'[a-z0-9]+', text.casefold())) - {
            'the', 'and', 'of', 'in', 'on', 'after', 'before', 'timeline',
            'events', 'event', 'latest', 'later', 'developments', 'results'}
    a, b = tokens(left), tokens(right)
    years_a = {w for w in a if re.fullmatch(r'(19|20)\d{2}', w)}
    years_b = {w for w in b if re.fullmatch(r'(19|20)\d{2}', w)}
    if years_a and years_b and years_a != years_b:
        return False  # Different periods deserve separate probes.
    return bool(a and b) and len(a & b) / len(a | b) >= 0.6


def check_search_progress(memory: dict, query: str, strategy: str) -> None:
    if not memory.get('progress_control_enabled'):
        return
    history = memory.get('search_history', [])
    if len(history) >= 3 and all(h.get('strategy') == strategy for h in history[-3:]):
        raise ValueError('Three consecutive searches used this strategy; probe another interval/facet/boundary strategy')
    exhausted = [h for h in history if h.get('new_verified_event_count') == 0
                 and h.get('evidence_update_count') == 0
                 and similar_intent(query, h['query'])]
    if len(exhausted) >= 2:
        raise ValueError('Similar intent already had two no-gain searches; change the event/period/facet or defer the uncertain direction')


def record_cycle_outcome(memory: dict, before: list[dict], after: list[dict]) -> dict:
    result = copy.deepcopy(memory)
    old = {e['event_id']: e for e in before}
    new_ids = [e['event_id'] for e in after if e['event_id'] not in old]
    evidence_updates = [e['event_id'] for e in after if e['event_id'] in old and
                        set(e.get('evidence_ids', [])) - set(old[e['event_id']].get('evidence_ids', []))]
    entry = result['search_history'][-1]
    entry.update(new_verified_event_count=len(new_ids), new_verified_event_ids=new_ids,
                 evidence_update_count=len(evidence_updates), evidence_updated_event_ids=evidence_updates)
    result['last_cycle_outcome'] = {k: copy.deepcopy(v) for k, v in entry.items()
                                    if k not in {'result_ids'}}
    if new_ids:
        # Readiness was assessed before VERIFY/MERGE; reconsider newly admitted events.
        result['skeleton_ready'] = False
    return result


def validate_deferred_directions(rows: object, memory: dict) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > 8:
        raise ValueError('deferred_directions must be a list of at most 8')
    history = {h['query']: h for h in memory.get('search_history', [])}
    checked = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('deferred direction must be an object')
        for key in ('direction', 'reason'):
            if not isinstance(row.get(key), str) or not 6 <= len(row[key].strip()) <= 400:
                raise ValueError('Deferred direction needs a brief direction and evidence-grounded reason')
        queries = row.get('attempted_queries')
        if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
            raise ValueError('Deferred direction needs attempted_queries')
        queries = list(dict.fromkeys(queries))
        if len(queries) < 2 or any(q not in history or
                history[q].get('new_verified_event_count') != 0 or
                history[q].get('evidence_update_count') != 0 for q in queries):
            raise ValueError('Deferral requires two recorded no-gain completed searches, not merely no new documents')
        if not any(similar_intent(a, b) for i, a in enumerate(queries) for b in queries[i + 1:]):
            raise ValueError('Deferred searches must include two comparable lexical intents')
        checked.append({'direction': row['direction'].strip(), 'reason': row['reason'].strip(),
                        'attempted_queries': queries, 'status': 'DEFERRED_UNCONFIRMED'})
    return checked
