"""Quarantine invalid candidates after bounded repair, never weaken verification."""
import copy

from .full_timeline import validate_verified_candidates
from .tisa_rollout import validate_thought


def quarantine_invalid_candidates(payload, documents, maximum, strict_dates=True):
    accepted, quarantined, seen = [], [], set()
    rows = payload.get('candidates', []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    for index, row in enumerate(rows):
        try:
            if index >= maximum:
                raise ValueError('Candidate batch limit exceeded')
            checked = validate_verified_candidates([row], documents, 1, strict_dates=strict_dates)[0]
            if checked['candidate_id'] in seen:
                raise ValueError('Duplicate candidate ID')
            if type(checked.get('relevance_pass')) is not bool or type(checked.get('contribution_pass')) is not bool:
                raise ValueError('VERIFY filters must be Boolean')
            seen.add(checked['candidate_id'])
            accepted.append(checked)
        except (ValueError, KeyError, TypeError) as error:
            quarantined.append({'raw_candidate': copy.deepcopy(row), 'error': str(error), 'training_target': False})
    try:
        thought = validate_thought(payload.get('thought'))
    except (AttributeError, ValueError):
        thought = 'Keep only independently validated candidates; unresolved extraction remains open.'
    return {'thought': thought, 'candidates': accepted, 'extraction_complete': False,
            'repair_exhausted': True, 'quarantined_candidates': quarantined,
            'training_target': False}
