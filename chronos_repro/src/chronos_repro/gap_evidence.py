"""Reuse existing proof as review context, never as automatic gap resolution."""
import copy
import math
import re
from collections import Counter

from .batch_memory import event_view
from .gap_contract import gap_signature

_STOP = set('a an the of in on at to for and or was were is are be been did do does has have had '
            'what when who how whether after before with by from as it its their that this these those '
            'would could should will result results announced official'.split())


def words(text):
    return set(re.findall(r'[a-z0-9]+', text.lower())) - _STOP


def deduplicate_open_gaps(memory):
    """Keep historical queries/IDs, but schedule one canonical copy of an exact question."""
    groups = {}
    for gap in memory.get('gaps', []):
        if gap.get('duplicate_of'):
            continue
        if not str(gap.get('retrieval_target', {}).get('question', '')).strip():
            continue  # Legacy rows without a question are not evidence of equivalence.
        groups.setdefault(gap_signature(gap), []).append(gap)
    for group in groups.values():
        if len(group) < 2:
            continue
        canonical = next((g for g in group if g['status'] == 'RESOLVED'), group[0])
        for gap in group:
            if gap is canonical or gap['status'] != 'OPEN':
                continue
            gap.update(status='DEFERRED', duplicate_of=canonical['gap_id'],
                       status_reason='duplicate_question_deferred_to_canonical_gap')
            memory.setdefault('duplicate_gap_links', []).append({
                'gap_id': gap['gap_id'], 'canonical_gap_id': canonical['gap_id'],
                'resolution_established': False, 'training_target': False})


def review_context(events, gap, gaps, limit=12):
    by_id = {e['event_id']: e for e in events}
    target = gap.get('retrieval_target', {})
    query_words = words(target.get('question', '') + ' ' + target.get('completion_criterion', ''))
    proofs = []
    for previous in gaps:
        if previous['gap_id'] == gap['gap_id'] or previous['status'] != 'RESOLVED':
            continue
        previous_words = words(previous.get('retrieval_target', {}).get('question', ''))
        overlap = len(query_words & previous_words)
        if gap_signature(previous) != gap_signature(gap) and overlap < 2:
            continue
        for proof in previous.get('resolution_evidence', []):
            event = by_id.get(proof.get('event_id'))
            quote = proof.get('quote')
            if (event is None or event.get('conflict') or not event.get('evidence_ids')
                    or not isinstance(quote, str) or len(quote) < 6 or quote not in event['summary']):
                continue
            proofs.append({'source_gap_id': previous['gap_id'], 'event_id': event['event_id'],
                           'quote': quote, 'relevance_overlap': overlap})
    proofs.sort(key=lambda p: (-p['relevance_overlap'], p['source_gap_id'], p['event_id']))
    proofs = proofs[:4]
    anchors = {gap.get('left_event_id'), gap.get('right_event_id')} - {None}
    proof_ids = {p['event_id'] for p in proofs}
    event_words = {key: words(e['summary']) for key, e in by_id.items()}
    frequencies = Counter(w for terms in event_words.values() for w in terms)
    def rank(event):
        key = event['event_id']
        score = sum(math.log((len(events) + 1) / (frequencies[w] + 1)) + 1
                    for w in query_words & event_words[key])
        return (key not in anchors, key not in proof_ids, -score, key)
    selected = sorted(events, key=rank)[:limit]
    visible_ids = {e['event_id'] for e in selected}
    return ([event_view(e) for e in selected],
            copy.deepcopy([p for p in proofs if p['event_id'] in visible_ids]))
