"""Gold-free model views; the full archive remains outside model context."""
from __future__ import annotations

import copy
import re


def _terms(text):
    return set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', str(text).lower()))


def _events(rows):
    return sorted(({k: copy.deepcopy(row[k]) for k in ('event_id', 'time', 'summary', 'conflict')
                    if k in row} for row in rows),
                  key=lambda row: (str(row.get('time', '')), row.get('event_id', '')))


def build_policy_state(state, *, purpose, local_event_limit=16, recent_query_limit=8):
    """Distinct exploration, gap discovery, repair and stop views.

    Counts represent archive state, never proof of semantic completeness.
    Limits apply to local repair only: global discovery retains the node directory.
    """
    if purpose not in {'explore', 'discover_gaps', 'repair_gap', 'stop'}:
        raise ValueError('Unknown policy purpose')
    for value in (local_event_limit, recent_query_limit):
        if type(value) is not int or value < 1:
            raise ValueError('View limits must be positive integers')
    if state.get('view_version') == 'two-phase-v7':
        if state.get('purpose') != purpose:
            raise ValueError('Build a different purpose from archive, not from a projected view')
        return copy.deepcopy(state)
    events = _events(state.get('events', state.get('timeline_events', [])))
    memory = state.get('memory', state.get('exploration_memory', {}))
    exploration = memory.get('exploration', memory)
    history = list(exploration.get('search_history', []))
    if 'exploration' in memory:
        history += memory.get('search_history', [])
    stages = [{k: copy.deepcopy(s[k]) for k in ('period', 'description', 'evidence_ids')
               if k in s} for s in exploration.get('stage_outline', [])]
    gaps = memory.get('gaps', [])
    result = {k: copy.deepcopy(state[k]) for k in
              ('dataset', 'topic', 'phase', 'keywords', 'valid_actions') if k in state}
    result.update(view_version='two-phase-v7', purpose=purpose, events=events)
    if state.get('corpus_profile'):
        result['corpus_profile'] = copy.deepcopy(state['corpus_profile'])
    recent = [{k: copy.deepcopy(q[k]) for k in
               ('query', 'gap_id', 'strategy', 'date_filter', 'new_verified_event_count',
                'evidence_update_count', 'skipped') if k in q}
              for q in history[-recent_query_limit:]]
    result['memory'] = {
        'stage_outline': stages,
        'recent_searches': recent,
        'search_history_count': len(history),
    }
    if purpose == 'explore':
        for key in ('boundary_assessment', 'observed_date_range', 'skeleton_ready',
                    'next_search_directions', 'deferred_directions', 'discovered_keywords'):
            if key in exploration:
                result['memory'][key] = copy.deepcopy(exploration[key])
    if purpose in {'explore', 'discover_gaps'}:
        leads = [r for r in exploration.get('lead_queue', []) if r.get('status') == 'OPEN']
        result['memory']['pending_leads'] = [{k: copy.deepcopy(r[k]) for k in ('lead_id', 'time', 'summary') if k in r}
                                             for r in leads[:6]]
        result['memory']['unresolved_lead_count'] = exploration.get('unresolved_lead_count', len(leads))
    if purpose != 'explore':
        result['memory']['gap_directory'] = [
            {k: copy.deepcopy(g[k]) for k in
             ('gap_id', 'type', 'status', 'description', 'left_event_id', 'right_event_id', 'retrieval_target')
             if k in g} for g in gaps]
    if purpose == 'repair_gap':
        gap = state.get('active_gap')
        if not gap:
            raise ValueError('Local repair requires an active gap')
        result['active_gap'] = copy.deepcopy(gap)
        anchors = {gap.get('left_event_id'), gap.get('right_event_id')} - {None, ''}
        positions = [i for i, e in enumerate(events) if e.get('event_id') in anchors]
        if anchors - {e.get('event_id') for e in events}:
            raise ValueError('Gap references an unknown event')
        mandatory = {i for p in positions for i in range(max(0, p - 2), min(len(events), p + 3))}
        terms = _terms(gap.get('retrieval_target', {}).get('question', gap.get('description', '')))
        ranked = sorted(range(len(events)), key=lambda i: (
            i not in mandatory, -len(terms & _terms(events[i].get('summary', ''))), i))
        selected = set(ranked[:max(local_event_limit, len(mandatory))])
        result['events'] = [e for i, e in enumerate(events) if i in selected]
        result['event_count'] = len(events)
        result['events_omitted_count'] = len(events) - len(result['events'])
        local_history = [q for q in history if q.get('gap_id') == gap.get('gap_id')]
        result['memory']['gap_search_history'] = [
            {k: copy.deepcopy(q[k]) for k in ('query', 'gap_id', 'date_filter',
             'new_verified_event_count', 'evidence_update_count', 'skipped') if k in q}
            for q in local_history[-recent_query_limit:]]
        result['memory']['known_gap_ids'] = [g['gap_id'] for g in gaps]
        # Other gaps are scheduler state, not the local worker's full context.
        result['memory'].pop('gap_directory', None)
        result['memory'].pop('stage_outline', None)
        result['memory'].pop('recent_searches', None)
        result['memory']['open_gap_count'] = sum(g.get('status', 'OPEN') in
                                               {'OPEN', 'IN_PROGRESS', 'FAILED'} for g in gaps)
        result['memory']['deferred_gap_count'] = sum(g.get('status') == 'DEFERRED' for g in gaps)
    result['context_note'] = (
        'Only visible evidence supports facts. Missing archive entries are not resolved. '
        'Search history is a subset. No result is not evidence of completeness. '
        'An empty query skips one gap, not the whole timeline. '
        'Global discovery must inspect missing branches and boundaries, not just adjacent dates.'
    )
    return result
