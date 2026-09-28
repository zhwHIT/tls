"""Conservative evidence reuse and exact duplicate routing; no semantic guesses."""
from __future__ import annotations

import copy
import hashlib
import json


PROTOCOL_VERSION = 'verify-v4-joint-evidence-and-missing-reasons'


def stable_documents(documents):
    # Reader annotations describe selection, not the supplied evidence itself.
    ignored = {'reader_score', 'reader_origin', 'reader_date_filter'}
    return [{k: copy.deepcopy(v) for k, v in row.items() if k not in ignored}
            for row in documents]


def unique_extracted_events(rows):
    keyed = {json.dumps(row, sort_keys=True, ensure_ascii=False): copy.deepcopy(row) for row in rows}
    return [keyed[key] for key in sorted(keyed)]


def extraction_key(documents, state, config, maximum):
    payload = {'protocol': PROTOCOL_VERSION, 'documents': stable_documents(documents),
               'state': state, 'model': config.get('model'), 'max_candidates': maximum,
               'strict_dates': config.get('strict_date_evidence', True),
               'temperature': config.get('temperature', 0),
               'repair_attempts': config['label_repair_attempts']}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _same_evidence(candidate, existing):
    event = candidate['event']
    return (candidate.get('status') == 'SUPPORTED' and not existing.get('conflict', False)
            and all(event.get(k) == existing.get(k) for k in ('time', 'summary', 'actors', 'location'))
            and bool(candidate.get('evidence_ids'))
            and set(candidate['evidence_ids']).issubset(existing.get('evidence_ids', []))
            and bool(candidate.get('date_evidence'))
            and candidate['date_evidence'] == existing.get('date_evidence')
            and candidate.get('confidence') == existing.get('confidence'))


def partition_exact_duplicates(candidates, timeline):
    """Drop only exact facts with no new evidence, dates, confidence or conflicts."""
    pending, operations, audit = [], [], []
    representatives = []
    for candidate in candidates:
        match = next((row for row in [*timeline, *representatives]
                      if _same_evidence(candidate, row)), None)
        if match is None:
            pending.append(candidate)
            if candidate.get('status') == 'SUPPORTED':
                representatives.append({**candidate['event'],
                    'event_id': None, 'representative_candidate_id': candidate['candidate_id'],
                    'evidence_ids': candidate.get('evidence_ids', []),
                    'date_evidence': candidate.get('date_evidence'),
                    'confidence': candidate.get('confidence'), 'conflict': False})
            continue
        operations.append({'candidate_id': candidate['candidate_id'], 'operation': 'DROP',
            'target_event_id': None, 'reason': 'Exact fact and date evidence already represented; no new evidence.'})
        audit.append({'candidate_id': candidate['candidate_id'],
            'existing_event_id': match.get('event_id'),
            'representative_candidate_id': match.get('representative_candidate_id'),
            'kind': 'exact_fact_and_evidence_duplicate', 'training_target': False})
    return pending, operations, audit
