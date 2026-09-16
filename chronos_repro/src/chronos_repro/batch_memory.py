"""Versioned factual memory for batched search control."""
from __future__ import annotations

import copy
import json


def event_view(event):
    return {k: copy.deepcopy(event[k]) for k in
            ('event_id', 'time', 'summary', 'conflict', 'evidence_ids') if k in event}


def initialize(state):
    return state.setdefault('controller_memory', {
        'version': 0, 'revision': 0, 'summary_revision': 0, 'facts': [],
        'event_snapshot': {}, 'pending_changes': [], 'batches_since_summary': 0,
        'query_history': [], 'summary_updates': 0})


def sync_events(state):
    memory = initialize(state)
    current = {e['event_id']: event_view(e) for e in state['timeline_events']}
    old = memory['event_snapshot']
    changes = []
    for key in sorted(set(old) | set(current)):
        if old.get(key) == current.get(key):
            continue
        memory['revision'] += 1
        changes.append({'revision': memory['revision'], 'event_id': key,
                        'operation': 'DELETE' if key not in current else ('UPDATE' if key in old else 'APPEND'),
                        'event': copy.deepcopy(current.get(key))})
    memory['pending_changes'].extend(changes)
    memory['event_snapshot'] = current
    changed_ids = {r['event_id'] for r in changes if r['operation'] != 'APPEND'}
    for gap in state.get('gap_memory', {}).get('gaps', []):
        if gap.get('status') != 'RESOLVED':
            continue
        supports = {p['event_id'] for p in gap.get('resolution_evidence', [])}
        supports.update({gap.get('left_event_id'), gap.get('right_event_id')} - {None})
        if supports & changed_ids:
            gap.update(status='OPEN', status_reason='support_changed_requires_review',
                       last_review_revision=None, resolution_evidence=[])
    return changes


def latest_delta(memory):
    """Latest replacement or deletion per ID, never drop corrections."""
    latest = {r['event_id']: r for r in memory['pending_changes']}
    return sorted(copy.deepcopy(list(latest.values())), key=lambda r: r['revision'])


def summary_due(state, *, interval=3, max_delta_chars=4500, force=False):
    memory = initialize(state)
    if not memory['pending_changes']:
        return False
    return (force or memory['batches_since_summary'] >= interval or
            len(json.dumps(latest_delta(memory), ensure_ascii=False)) > max_delta_chars)


def commit_summary(state, facts, revision):
    memory = initialize(state)
    if revision != memory['revision']:
        raise ValueError('Summary was generated from a stale event revision')
    memory.update(facts=copy.deepcopy(facts), summary_revision=revision,
                  version=memory['version'] + 1, pending_changes=[], batches_since_summary=0,
                  summary_updates=memory['summary_updates'] + 1)


def record_query(state, row):
    initialize(state)['query_history'].append(copy.deepcopy(row))


def policy_view(state, phase, active_gap=None, *, recent_queries=6):
    memory = initialize(state)
    history = memory['query_history']
    return {
        'task': state.get('task_description', state['topic']), 'phase': phase,
        'keywords': state.get('keywords', [])[:12],
        'memory': {'version': memory['version'], 'through_revision': memory['summary_revision'],
                   'summary_update_deferred': memory.get('summary_failure_revision') == memory['revision'],
                   'facts': copy.deepcopy(memory['facts'])},
        'current_revision': memory['revision'], 'event_delta': latest_delta(memory),
        'active_gap': copy.deepcopy(active_gap),
        'active_gap_events': [event_view(e) for e in state['timeline_events']
                              if active_gap and e['event_id'] in {active_gap.get('left_event_id'), active_gap.get('right_event_id')}],
        'gap_counts': {status: sum(g.get('status') == status for g in state.get('gap_memory', {}).get('gaps', []))
                       for status in ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'DEFERRED', 'NO_SEARCH_NEEDED')},
        'recent_queries': [{k: copy.deepcopy(r[k]) for k in
                            ('query', 'time_filter', 'gap_id', 'batch_event_change_count', 'gain_attribution') if k in r}
                           | {'result_count': len(r.get('result_ids', []))} for r in history[-recent_queries:]],
        'older_query_count': max(0, len(history) - recent_queries),
        'pending_leads': [{k: copy.deepcopy(r[k]) for k in ('lead_id', 'time', 'summary', 'status') if k in r}
                          for r in state.get('exploration_memory', {}).get('lead_queue', [])[:4]],
        'event_count': len(state['timeline_events']),
    }
