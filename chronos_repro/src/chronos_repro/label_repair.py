"""Explicit length contracts and auditable, conservative memory recovery."""
from __future__ import annotations

import copy
import re

BOUNDARY_MAX_CHARS = 600
BOUNDARY_TARGET_CHARS = 320


class ResponseParseError(ValueError):
    def __init__(self, message, response, audit):
        super().__init__(message)
        self.response = response
        self.audit = audit


def repair_details(payload, error, attempt):
    result = {'attempt': attempt, 'previous_response': copy.deepcopy(payload),
              'previous_error': str(error),
              'instruction': 'Return the complete corrected JSON. Preserve valid facts and fields; fix the reported violation.'}
    if getattr(error, 'repair_context', None):
        result['constraints'] = copy.deepcopy(error.repair_context)
        result['instruction'] += ' ' + error.repair_context.get('instruction', '')
    match = re.search(r'([a-z_]+) must be nonempty text <= (\d+) characters', str(error))
    if match:
        field, limit = match.group(1), int(match.group(2))
        value = payload.get(field) if isinstance(payload, dict) else None
        result['field_constraint'] = {'field': field, 'required_type': 'nonempty string',
                                      'hard_max_chars': limit, 'target_max_chars': max(1, limit // 2),
                                      'previous_chars': len(value) if isinstance(value, str) else None}
        result['instruction'] += (
            f' Rewrite {field} as one short sentence, at most {max(1, limit // 2)} characters including spaces. '
            'Do not list every stage/date. Preserve uncertainty and negation; do not claim new evidence or completion.')
    return result


def recover_boundary_summary(payload, error, validator):
    """Only an overlong boundary note can fall back. Never accept/truncate bad facts."""
    if not isinstance(payload, dict) or payload.get('action') != 'MEMORY_UPDATE':
        raise ValueError('Boundary fallback requires a MEMORY_UPDATE object')
    value = payload.get('boundary_assessment')
    if ('boundary_assessment must be nonempty text' not in str(error) or
            not isinstance(value, str) or len(value) <= BOUNDARY_MAX_CHARS):
        raise ValueError('This error is not a recoverable overlong boundary summary')
    candidate = copy.deepcopy(payload)
    candidate['boundary_assessment'] = (
        'The latest boundary assessment failed length validation. Beginning and ending coverage '
        'remain unconfirmed; consult existing evidence and probe unresolved boundaries.')
    candidate['skeleton_ready'] = False
    candidate['thought'] = 'Keep validated updates, but leave boundary completeness unconfirmed.'
    checked = validator(candidate)  # Every other field must independently pass unchanged.
    return checked, {'field': 'boundary_assessment', 'raw_value': value,
                     'raw_chars': len(value), 'reason': str(error),
                     'training_target': False, 'skeleton_ready_forced_false': True}
