"""Bounded, rotating views of unresolved evidence; no factual promotion by ranking."""
from __future__ import annotations

import copy


def select_leads(state, limit=8):
    if type(limit) is not int or limit < 1:
        raise ValueError('pending_lead_limit must be a positive integer')
    rows = [r for r in state.get('exploration_memory', {}).get('lead_queue', [])
            if r.get('status') == 'OPEN']
    # Every unseen lead precedes every previously exposed lead, regardless of priority.
    ranked = sorted(rows, key=lambda r: (r.get('presentation_count', 0),
                    r.get('last_presented_turn', -1), -r.get('priority', 0), r['lead_id']))
    selected = []
    for row in ranked[:limit]:
        view = {k: copy.deepcopy(row[k]) for k in ('lead_id', 'time', 'summary', 'status', 'reason') if k in row}
        view['reason'] = row.get('reason') or 'Legacy candidate has no recorded reason; inspect its source evidence before inferring a date.'
        view['attempted_queries'] = copy.deepcopy(row.get('attempted_queries', [])[-2:])
        view['attempt_count'] = len(row.get('attempted_queries', []))
        selected.append(view)
    return selected, {'open': len(rows), 'shown': len(selected), 'omitted': len(rows) - len(selected),
                      'never_shown': sum(not r.get('presentation_count', 0) for r in rows)}


def record_presentation(state, visible):
    memory = state.setdefault('exploration_memory', {})
    turn = memory.get('lead_presentation_turn', 0) + 1
    ids = {r['lead_id'] for r in visible}
    for row in memory.get('lead_queue', []):
        if row['lead_id'] in ids:
            row['presentation_count'] = row.get('presentation_count', 0) + 1
            row['last_presented_turn'] = turn
    memory['lead_presentation_turn'] = turn


def record_lead_query(state, record):
    ids = set(record.get('target_lead_ids', []))
    for row in state.get('exploration_memory', {}).get('lead_queue', []):
        if row['lead_id'] in ids:
            attempted = row.setdefault('attempted_queries', [])
            if record['query'] not in attempted:
                attempted.append(record['query'])
            row['last_search_batch'] = record['batch_id']
            row['last_result_count'] = len(record['result_ids'])
