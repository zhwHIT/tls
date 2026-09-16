"""Factual summary validation and loss-recorded length recovery."""
from __future__ import annotations

import copy


def fact_text_limits(settings=None):
    settings = settings or {}
    maximum = settings.get('summary_fact_max_chars', 200)
    target = settings.get('summary_fact_target_chars', 140)
    if type(maximum) is not int or type(target) is not int or not 6 <= target <= maximum:
        raise ValueError('Fact text limits require integer 6 <= target <= maximum')
    return maximum, target


class FactLengthError(ValueError):
    def __init__(self, issues, max_chars=200, target_chars=140):
        self.issues = issues
        self.repair_context = {
            'invalid_fields': issues, 'hard_max_chars': max_chars, 'target_max_chars': target_chars,
            'instruction': f'Rewrite ONLY the listed overlong texts as concise sentences of at most {target_chars} '
                           'characters including spaces. Preserve uncertainty, negation and event_ids. '
                           'Return the full corrected object, not the unchanged previous response.'}
        super().__init__(f'Fact text exceeds {max_chars} characters: ' + ', '.join(
            f"{r['field']} has {r['actual_chars']}" for r in issues))


def validate_facts(payload, allowed, current_ids, *, max_chars=200, target_chars=140):
    fact_text_limits({'summary_fact_max_chars': max_chars, 'summary_fact_target_chars': target_chars})
    rows = payload.get('facts')
    if payload.get('action') != 'MEMORY_UPDATE' or not isinstance(rows, list) or len(rows) > 8:
        raise ValueError('MEMORY_UPDATE requires at most 8 factual rows')
    issues = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or set(row) != {'text', 'event_ids'} or
                not isinstance(row['text'], str) or len(row['text']) < 6):
            raise ValueError(f'facts[{index}] requires text of 6-{max_chars} characters and event_ids')
        ids = row['event_ids']
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
                or not set(ids) <= allowed & current_ids):
            raise ValueError(f'facts[{index}] cites an absent or unseen event')
        if len(row['text']) > max_chars:
            issues.append({'field': f'facts[{index}].text', 'index': index,
                           'actual_chars': len(row['text'])})
    if allowed & current_ids and not rows:
        raise ValueError('Do not erase all factual memory when supported facts remain')
    if issues:
        raise FactLengthError(issues, max_chars, target_chars)
    return {'action': 'MEMORY_UPDATE', 'facts': copy.deepcopy(rows)}


def recover_fact_lengths(payload, allowed, current_ids, events, *, max_chars=200, target_chars=140):
    """Select intact valid rows; never truncate text or accept invalid provenance."""
    try:
        return validate_facts(payload, allowed, current_ids, max_chars=max_chars, target_chars=target_chars), {}
    except FactLengthError as error:
        omitted = {i['index'] for i in error.issues}
        rows = [copy.deepcopy(r) for i, r in enumerate(payload['facts']) if i not in omitted]
        mode = 'select_valid_rows'
        if not rows:
            # An entire source summary is eligible; a chopped sentence is not.
            rows = [{'text': e['summary'], 'event_ids': [e['event_id']]} for e in events
                    if e['event_id'] in allowed & current_ids and 6 <= len(e['summary']) <= max_chars][:8]
            mode = 'select_intact_event_summaries'
        audit = {'kind': 'fact_length_selection', 'mode': mode,
                 'omitted_rows': [copy.deepcopy(payload['facts'][i]) for i in sorted(omitted)],
                 'events_deleted': False, 'training_target': False}
        if not rows:
            return None, {**audit, 'mode': 'retain_previous_summary_and_delta'}
        return validate_facts({'action': 'MEMORY_UPDATE', 'facts': rows}, allowed, current_ids,
                              max_chars=max_chars, target_chars=target_chars), audit
