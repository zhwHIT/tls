"""Versioned factual memory for batched search control."""
from __future__ import annotations

import copy
import json


def event_view(event):
    return {k: copy.deepcopy(event[k]) for k in
            ('event_id', 'time', 'summary', 'conflict', 'evidence_ids', 'date_precision', 'time_range') if k in event}


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


def batch_feedback(state, phase):
    """One observation per committed batch; partial resumes cannot prove zero gain."""
    history = initialize(state)['query_history']
    phases = {r['batch_id']: 'GAP_REFINEMENT' if r.get('gap_id') else 'SKELETON_EXPLORATION'
              for r in history if 'batch_id' in r}
    batches = {r['batch_id']: r for r in state.get('query_batches', [])}
    rows = [r for _, r in sorted(batches.items()) if r.get('phase', phases.get(r['batch_id'], phase)) == phase]
    trailing_zero = 0
    for row in reversed(rows):
        if row.get('continued_partial_batch') or row.get('event_change_count') != 0:
            break
        trailing_zero += 1
    return {'recent_completed_batches': [
        {k: copy.deepcopy(r[k]) for k in ('batch_id', 'query_count', 'event_change_count',
            'appended_event_count', 'updated_event_count', 'deleted_event_count',
            'continued_partial_batch', 'gain_scope') if k in r} for r in rows[-4:]],
        'consecutive_zero_change_batches': trailing_zero,
        'gain_unit': 'event_state_changes_per_joint_batch_not_semantic_coverage'}


def policy_view(state, phase, active_gap=None, *, recent_queries=6, pending_lead_limit=8):
    memory = initialize(state)
    history = memory['query_history']
    event_first_phase = state.get('_preextracted_events', False)
    lead_view = {}
    if not event_first_phase:
        from .lead_feedback import select_leads
        leads, lead_counts = select_leads(state, pending_lead_limit)
        lead_view = {'pending_leads': leads, 'pending_lead_counts': lead_counts}
    query_fields = ('query', 'time_filter', 'gap_id')
    if not event_first_phase:
        query_fields += ('target_lead_ids', 'batch_event_change_count', 'gain_attribution')
    return {
        'task': state.get('task_description', state['topic']), 'phase': phase,
        **({'retrieval_contract': {
                'ranking': 'article BM25+dense retrieval',
                'returned_content': 'pre-extracted events and times, no article body',
                'extraction_errors_accepted': True,
                'verify_role': 'topic relevance and timeline membership only'},
            'retrieval_feedback': {k: copy.deepcopy(v)
                for k, v in state.get('retrieval_feedback', {}).items()
                if k in {'last_batch_articles', 'articles_without_events', 'candidate_events',
                         'unread_candidate_events', 'raw_article_returned',
                         'deferred_verification_count', 'deferred_verification_scope'}}}
           if state.get('_preextracted_events') else {}),
        **({'event_scope': copy.deepcopy(state['_event_scope'])} if state.get('_event_scope') else {}),
        'keywords': state.get('keywords', [])[:12],
        'memory': {'version': memory['version'], 'through_revision': memory['summary_revision'],
                   'summary_update_deferred': memory.get('summary_failure_revision') == memory['revision'],
                   'facts': copy.deepcopy(memory['facts'])},
        'current_revision': memory['revision'], 'event_delta': latest_delta(memory),
        **({} if event_first_phase else {'active_gap': copy.deepcopy(active_gap),
        'active_gap_events': [event_view(e) for e in state['timeline_events']
                              if active_gap and e['event_id'] in {active_gap.get('left_event_id'), active_gap.get('right_event_id')}],
        'gap_counts': {status: sum(g.get('status') == status for g in state.get('gap_memory', {}).get('gaps', []))
                       for status in ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'DEFERRED', 'NO_SEARCH_NEEDED')}}),
        'recent_queries': [{k: copy.deepcopy(r[k]) for k in query_fields if k in r}
                           | {'result_count': len(r.get('result_ids', []))} for r in history[-recent_queries:]],
        'older_query_count': max(0, len(history) - recent_queries),
        **lead_view,
        'event_count': len(state['timeline_events']),
        **({} if event_first_phase else {'batch_feedback': batch_feedback(state, phase)}),
    }
