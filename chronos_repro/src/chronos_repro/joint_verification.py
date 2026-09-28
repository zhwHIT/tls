"""Assemble already selected evidence for joint VERIFY, preserving source boundaries."""
from __future__ import annotations

import copy
import json
import re

from .evidence_access import terms


def same_source(left, right):
    return bool(left.get('document_id') and left.get('document_sha256')
                and left['document_id'] == right.get('document_id')
                and left['document_sha256'] == right.get('document_sha256'))


def related_passages(seed, available, topic):
    ignored = terms(topic.replace('_', ' ')) | {
        'said', 'says', 'will', 'would', 'could', 'year', 'years', 'today', 'yesterday'}
    words = terms(seed.get('text', '')) - ignored
    ranked = []
    for row in available:
        if row['id'] == seed['id']:
            continue
        overlap = len(words & (terms(row.get('text', '')) - ignored))
        same = same_source(seed, row)
        if not same and overlap < 3:
            continue
        distance = abs(seed.get('source_start', 0) - row.get('source_start', 0))
        dated = bool(row.get('temporal_annotations') or re.search(r'\b\d{4}\b', row.get('text', '')))
        ranked.append(((not same, -overlap if not same else distance, not dated, row['id']), row))
    return [row for _, row in sorted(ranked, key=lambda item: item[0])]


def expand_batch(seeds, selected, state, settings):
    """Only add selected passages or source passages of previously extracted candidates."""
    maximum = max(len(seeds), settings.get('joint_verification_max_passages', 4))
    max_chars = settings.get('joint_verification_max_chars', 6000)
    reader_passages = getattr(state.get('_evidence_reader'), 'passages', {})
    old_ids = {i for c in state.get('candidate_pool', [])
               if c['status'] == 'INSUFFICIENT' and c.get('relevance_pass') and not c.get('resolution_event_ids')
               for i in c.get('source_passage_ids', [])}
    available = {i: reader_passages[i] for i in old_ids if i in reader_passages}
    available.update({p['id']: p for p in selected})
    batch = list(seeds)
    ids = {p['id'] for p in batch}
    def size(row):
        return sum(len(str(row.get(k, ''))) for k in ('title', 'context_before', 'text'))
    chars = sum(size(p) for p in batch)
    for seed in seeds:
        eligible = [p for p in available.values() if same_source(seed, p) or p['id'] in old_ids]
        for row in related_passages(seed, eligible, state['topic']):
            if len(batch) >= maximum:
                return batch
            if row['id'] in ids or chars + size(row) > max_chars:
                continue
            batch.append(row)
            ids.add(row['id'])
            chars += size(row)
    return batch


def request_fits(client, instruction, system, config):
    """Mirror the actual compact projection before inspecting the existing token guard."""
    from .compact_context import compact_instruction, serialize
    compact = config.get('compact_context', {})
    payload = compact_instruction(instruction, compact) if compact.get('enabled') else instruction
    content = serialize(payload) if compact.get('enabled') else json.dumps(payload, ensure_ascii=False)
    if len(system) + len(content) > compact.get('max_request_chars', 64000):
        return False
    while client is not None:
        if hasattr(client, 'estimate'):
            return client.estimate([{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]) <= client.settings.get('preflight_limit', 3800)
        client = getattr(client, 'client', None)
    return True


def pending_candidates(state, batch, maximum=6):
    ids = {p['id'] for p in batch}
    rows = [c for c in state.get('candidate_pool', [])
            if c['status'] == 'INSUFFICIENT' and c.get('relevance_pass')
            and not c.get('resolution_event_ids') and set(c.get('evidence_ids', [])) & ids]
    rows.sort(key=lambda c: (not c.get('contribution_pass'), c['candidate_id']))
    result = []
    for row in rows[:maximum]:
        view = {k: copy.deepcopy(row[k]) for k in ('candidate_id', 'event', 'reason', 'evidence_ids') if k in row}
        if row.get('latest_reason'):
            view['reason'] = row['latest_reason']
        # The model must see only citeable sources in this request, not unavailable old IDs.
        view['evidence_ids'] = [i for i in view['evidence_ids'] if i in ids]
        result.append(view)
    return result


def record_resolutions(state, candidates, applied):
    """Close only explicitly reverified candidates whose replacement was committed by MERGE."""
    events = {e['event_id']: e for e in state['timeline_events']}
    committed = {r['candidate_id']: r['event_id'] for r in applied
                 if r.get('applied_operation') in {'APPEND', 'UPDATE'} and r.get('event_id') in events}
    pool = {c['candidate_id']: c for c in state.get('candidate_pool', [])}
    for row in candidates:
        if row['status'] == 'INSUFFICIENT':
            for old_id in row.get('revises_candidate_ids', []):
                if old_id in pool and pool[old_id]['status'] == 'INSUFFICIENT':
                    pool[old_id]['latest_reason'] = row['reason']
                    pool[old_id]['last_reassessment_candidate_id'] = row['candidate_id']
        event_id = committed.get(row['candidate_id'])
        if (row['status'] != 'SUPPORTED' or not event_id or events[event_id].get('conflict')
                or events[event_id]['time'] != row['event']['time']):
            continue
        for old_id in row.get('revises_candidate_ids', []):
            if old_id in pool and pool[old_id]['status'] == 'INSUFFICIENT':
                pool[old_id]['resolution_event_ids'] = [event_id]
