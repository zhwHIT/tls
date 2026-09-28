"""Deterministic, Gold-free event-time partitions and isolated worker state."""
from __future__ import annotations
import copy
from collections import defaultdict
from datetime import date, timedelta
from .evidence_access import terms


def in_scope(day, scope):
    try:
        return date.fromisoformat(scope['start']) <= date.fromisoformat(str(day)) <= date.fromisoformat(scope['end'])
    except (TypeError, ValueError):
        return False


def intersect(scope, requested):
    for key in ('start', 'end'):
        date.fromisoformat(scope[key])
    start = max(scope['start'], requested.get('start') or scope['start'])
    end = min(scope['end'], requested.get('end') or scope['end'])
    date.fromisoformat(start); date.fromisoformat(end)
    return None if start > end else {'start': start, 'end': end, 'mode': 'hard', 'semantics': 'event_date'}


def choose_intervals(events, passages, processed=(), *, keywords=(), width_days=90,
                     maximum=3, min_documents=2):
    if any(type(x) is not int or x < 1 for x in (width_days, maximum, min_documents)):
        raise ValueError('Interval limits must be positive integers')
    bins = defaultdict(lambda: {'documents': set(), 'unread': set(), 'dates': set(), 'represented': set()})
    processed = set(processed)
    query = terms(' '.join(keywords))
    # Deduplicate syndicated content before estimating density.
    seen = set()
    for p in sorted(passages, key=lambda p: str(p['id'])):
        if query and not query & terms(p.get('title', '') + ' ' + p.get('text', '')):
            continue
        digest = p.get('content_sha256', p['id'])
        if digest in seen:
            continue
        seen.add(digest)
        for a in p.get('temporal_annotations', []):
            try:
                day = date.fromisoformat(a['date'])
            except (KeyError, TypeError, ValueError):
                continue
            b = bins[(day.toordinal() - 1) // width_days]
            b['documents'].add(p['document_id']); b['dates'].add(day.isoformat())
            if p['id'] not in processed:
                b['unread'].add(digest)
    for e in events:
        key = (date.fromisoformat(e['time']).toordinal() - 1) // width_days
        if key in bins:
            bins[key]['represented'].add(e['time'])
    rows = []
    for key, b in bins.items():
        if len(b['documents']) < min_documents or not b['unread']:
            continue
        start = date.fromordinal(key * width_days + 1)
        end = date.fromordinal(min(date.max.toordinal(), start.toordinal() + width_days - 1))
        deficit = len(b['dates'] - b['represented'])
        score = (len(b['documents']) + deficit + len(b['unread']) ** .5) / (1 + len(b['represented']))
        rows.append({'interval_id': f'interval-{key}', 'start': start.isoformat(), 'end': end.isoformat(),
                     'score': score, 'document_count': len(b['documents']), 'unread_count': len(b['unread']),
                     'candidate_dates': len(b['dates']), 'represented_dates': len(b['represented']),
                     'interpretation': 'evidence_supported_exploration_priority_not_confirmed_missing_events'})
    chosen = sorted(rows, key=lambda r: (-r['score'], r['start']))[:maximum]
    return sorted(chosen, key=lambda r: r['start'])


def fresh_worker(parent, scope):
    return {'dataset': parent['dataset'], 'topic': parent['topic'],
            'task_description': parent.get('task_description', parent['topic']),
            'keywords': copy.deepcopy(parent.get('keywords', [])), 'timeline_events': [],
            'search_history': [], 'query_batches': [], 'candidate_pool': [],
            'exploration_memory': {}, 'gap_memory': {'gaps': [], 'history': []},
            '_event_scope': copy.deepcopy(scope), '_supplement_worker': True,
            '_frozen_topic_path': parent.get('_frozen_topic_path')}


def gap_exhausted(gap, config):
    if 'phase2_max_batches_per_gap' in config:
        return gap.get('search_batches', 0) >= config['phase2_max_batches_per_gap']
    return len(gap.get('attempted_queries', [])) >= config.get('phase2_max_attempts_per_gap', 3)


def validate_settings(config):
    settings = config.get('phase1_supplement', {})
    if not settings.get('enabled'):
        return
    if not config.get('batch_controller', {}).get('enabled') or not config.get('coverage_pipeline', {}).get('enabled'):
        raise ValueError('Supplement requires batched coverage execution')
    if config.get('phase2_teacher_guidance', True):
        raise ValueError('Supplement must not use Gold-guided policy')
    if settings.get('workers', 1) != 1:
        raise ValueError('Shared API ledger supports serial supplement workers only')
    for key in ('max_intervals', 'interval_days', 'min_documents', 'batches_per_interval', 'reread_passages', 'reread_pages'):
        if type(settings.get(key)) is not int or settings[key] < 1:
            raise ValueError('Missing/invalid supplement setting: ' + key)
    if settings['reread_pages'] > 4:
        raise ValueError('Supplement reread_pages must be at most four')
    for key in ('phase2_max_batches_per_gap', 'phase2_max_gap_cycles'):
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError('Explicit phase-two batch budgets required')
