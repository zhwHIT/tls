"""Repair invalid VERIFY rows while retaining independently validated rows locally."""
from __future__ import annotations

import copy
from collections import Counter

from .compact_context import ContextLimitError
from .full_timeline import validate_verified_candidates
from .label_repair import ResponseParseError
from .tisa_rollout import validate_thought


def _checked(row, documents, strict_dates, pending_candidates=()):
    if not isinstance(row, dict):
        raise ValueError('Candidate must be an object')
    result = validate_verified_candidates([row], documents, 1, strict_dates=strict_dates)[0]
    # Runtime lifecycle/provenance fields cannot be supplied by the model.
    for key in ('merge_processed', 'source_passage_ids', 'resolution_event_ids', 'latest_reason',
                'last_reassessment_candidate_id'):
        result.pop(key, None)
    if any(type(result.get(k)) is not bool for k in ('relevance_pass', 'contribution_pass')):
        raise ValueError('VERIFY filters must be Boolean')
    if result['status'] == 'INSUFFICIENT' and (not isinstance(result.get('reason'), str) or len(result['reason'].strip()) < 6):
        raise ValueError('INSUFFICIENT requires reason stating the missing evidence and what would resolve it')
    revisions = result.get('revises_candidate_ids', [])
    if (not isinstance(revisions, list) or any(not isinstance(i, str) for i in revisions)
            or len(revisions) != len(set(revisions))):
        raise ValueError('revises_candidate_ids must be distinct supplied pending candidate IDs')
    allowed = {c['candidate_id']: c for c in pending_candidates}
    for old_id in revisions:
        if old_id not in allowed or not set(allowed[old_id]['evidence_ids']) & set(result['evidence_ids']):
            raise ValueError('Revised candidate must be supplied and its original evidence must be cited')
    result.pop('repair_index', None)
    return result


def verify_with_partial_repair(client, instruction, documents, config, call):
    maximum = config['max_candidates_per_round']
    strict = config.get('strict_date_evidence', True)
    pending_candidates = instruction.get('state_before_verify', {}).get('memory', {}).get('pending_candidates', [])
    current = copy.deepcopy(instruction)
    frozen, pending, failures = {}, {}, []
    patch_mode = False
    initial_complete = False
    thought = 'Keep independently validated candidates; unresolved extraction remains open.'
    extra_quarantine = []
    for attempt in range(config['label_repair_attempts'] + 1):
        errors = []
        payload = None
        try:
            payload, audit = call(client, current, config['temperature'])
            failures.append(audit)
        except ResponseParseError as error:
            failures.extend([error.audit, {'validation_error': str(error), 'attempt': attempt + 1}])
            errors.append('Response must be one JSON object')
        except ContextLimitError as error:
            if not attempt:
                raise
            failures.append({'attempt': attempt + 1, 'validation_error': str(error),
                             'repair_context_limit': True, 'retained_candidate_count': len(frozen)})
            break
        if isinstance(payload, dict):
            try:
                thought = validate_thought(payload.get('thought'))
            except (ValueError, TypeError, AttributeError) as error:
                errors.append(str(error))
            complete = payload.get('extraction_complete')
            if type(complete) is not bool:
                errors.append('extraction_complete must be Boolean')
            rows = payload.get('candidates')
            if not isinstance(rows, list):
                errors.append('candidates must be a bounded list')
            elif patch_mode:
                # Reject changes to retained rows, invented slots, omissions and ambiguous slots.
                indexes = [r.get('repair_index') if isinstance(r, dict) else None for r in rows]
                if (any(type(i) is not int for i in indexes) or len(indexes) != len(set(indexes))
                        or set(indexes) != set(pending)):
                    errors.append('Return exactly one repair_index for every requested slot; no other rows')
                else:
                    used_ids = {r['candidate_id'] for r in frozen.values()}
                    candidate_ids = Counter(str(r.get('candidate_id')) for r in rows)
                    for row in rows:
                        index = row['repair_index']
                        try:
                            checked = _checked(row, documents, strict, pending_candidates)
                            if checked['candidate_id'] in used_ids or candidate_ids[checked['candidate_id']] > 1:
                                raise ValueError('Candidate ID duplicates a retained or repaired row')
                            frozen[index] = checked
                            used_ids.add(checked['candidate_id'])
                            del pending[index]
                        except (KeyError, TypeError, ValueError, AttributeError) as error:
                            pending[index] = {'raw_candidate': copy.deepcopy(row), 'error': str(error)}
            else:
                initial_complete = complete is True
                ids = Counter(str(r.get('candidate_id')) for r in rows if isinstance(r, dict))
                for index, row in enumerate(rows):
                    if index >= maximum:
                        extra_quarantine.append({'raw_candidate': copy.deepcopy(row),
                            'error': 'Candidate batch limit exceeded', 'training_target': False})
                        continue
                    try:
                        checked = _checked(row, documents, strict, pending_candidates)
                        if ids[checked['candidate_id']] > 1:
                            raise ValueError('Duplicate candidate ID')
                        frozen[index] = checked
                    except (KeyError, TypeError, ValueError, AttributeError) as error:
                        pending[index] = {'raw_candidate': copy.deepcopy(row), 'error': str(error)}
                patch_mode = True
            if patch_mode and not pending and not errors and not extra_quarantine:
                return {'thought': thought, 'candidates': [frozen[i] for i in sorted(frozen)],
                        'extraction_complete': initial_complete and complete is True,
                        **({'partial_repair_applied': True, 'training_target': False} if attempt else {})}, failures
        if pending:
            errors.extend(f'slot {i}: {item["error"]}' for i, item in pending.items())
        failures.append({'attempt': attempt + 1, 'validation_error': '; '.join(errors),
                         'retained_candidate_count': len(frozen), 'repair_slot_count': len(pending)})
        if attempt == config['label_repair_attempts'] or extra_quarantine:
            break
        current = copy.deepcopy(instruction)
        if patch_mode:
            template = copy.deepcopy(instruction['required_json']['candidates'][0])
            template['repair_index'] = 'integer slot index from repair.invalid_candidates'
            current['required_json']['candidates'] = [template] if pending else []
            current['objective'] = (
                'Repair ONLY the requested candidate slots and invalid metadata. Return no retained candidates. '
                'Use supplied evidence; preserve plans, uncertainty and negation. Never invent dates or quotes. '
                'Publication metadata alone cannot establish an event date. If evidence is inadequate, return '
                'INSUFFICIENT with time=null and omit date_evidence; reason must state the remaining gap '
                'and the evidence needed. Preserve valid revises_candidate_ids. Copy the shortest verbatim span that still '
                'contains the event and its date; do not truncate required context. Return every requested '
                'repair_index exactly once. This is repair, not additional extraction. Do not claim newly completed extraction.')
            current['limits']['maximum_candidates'] = len(pending)
            current['repair'] = {'attempt': attempt + 1, 'mode': 'invalid_candidates_only',
                'retained_candidate_ids': [r['candidate_id'] for r in frozen.values()],
                'invalid_candidates': [{'repair_index': i, **copy.deepcopy(item)} for i, item in pending.items()],
                'errors': errors, 'original_extraction_complete': initial_complete}
        else:
            current['repair'] = {'attempt': attempt + 1, 'mode': 'unparseable_batch', 'errors': errors,
                'instruction': 'Return the required JSON object. Never invent missing date components.'}
    quarantined = [{**copy.deepcopy(item), 'repair_index': i, 'training_target': False}
                   for i, item in pending.items()] + extra_quarantine
    return {'thought': thought, 'candidates': [frozen[i] for i in sorted(frozen)],
            'extraction_complete': False, 'repair_exhausted': True,
            'quarantined_candidates': quarantined, 'training_target': False}, failures
