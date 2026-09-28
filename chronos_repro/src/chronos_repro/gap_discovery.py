"""One visible anchor contract for gap discovery and its label repair."""
import copy

from .tisa_rollout import validate_gap_memory_action


def bind_anchors(window, facts, archive):
    by_id = {e['event_id']: e for e in archive}
    eligible = [f for f in facts if f.get('event_ids') and set(f['event_ids']) <= set(by_id)]
    anchors = {e['event_id']: e for e in window}
    for fact in eligible:
        for event_id in fact['event_ids']:
            anchors.setdefault(event_id, by_id[event_id])
    return copy.deepcopy(list(anchors.values())), copy.deepcopy(eligible)


def validate_discovery(payload, anchors):
    try:
        if not isinstance(payload, dict):
            raise ValueError('Gap discovery response must be an object')
        if isinstance(payload.get('gaps'), list) and any(not isinstance(g, dict) for g in payload['gaps']):
            raise ValueError('Each gap proposal must be an object')
        return validate_gap_memory_action(payload, anchors, 3, require_atomic=True)
    except ValueError as error:
        error.repair_context = {
            'allowed_anchor_ids': [e['event_id'] for e in anchors],
            'instruction': 'Use only allowed_anchor_ids. Copy anchor_quote verbatim from the matching '
                           'events[].summary, never from fact_memory. If no supported gap can be '
                           'formed, return gaps: []; do not invent an ID or rewrite an evidence quote.'}
        raise


def recover_discovery(payload, anchors):
    """Quarantine failed rows without interpreting a failure as absence of gaps."""
    valid, rejected = [], []
    if not isinstance(payload, dict) or payload.get('action') != 'GAP_MEMORY' or not isinstance(payload.get('gaps'), list):
        rejected.append({'reason': 'invalid_gap_discovery_envelope', 'raw_response': copy.deepcopy(payload)})
    else:
        # A response violating the batch-size contract is not silently truncated.
        if len(payload['gaps']) > 3:
            rejected.append({'reason': 'too_many_gap_proposals', 'raw_response': copy.deepcopy(payload)})
        else:
            for index, gap in enumerate(payload['gaps']):
                candidate = {'thought': 'Retain individually validated evidence-grounded gap proposals.',
                             'action': 'GAP_MEMORY', 'gaps': [gap]}
                try:
                    checked = validate_discovery(candidate, anchors)
                    valid.extend(checked['gaps'])
                except (ValueError, TypeError, KeyError) as error:
                    rejected.append({'index': index, 'reason': str(error), 'raw_gap': copy.deepcopy(gap)})
    return ({'thought': 'Partial recovery only; gap discovery remains incomplete.',
             'action': 'GAP_MEMORY', 'gaps': valid},
            {'kind': 'gap_discovery_partial_quarantine', 'rejected': rejected,
             'discovery_complete': False, 'training_target': False, 'completion_established': False})
