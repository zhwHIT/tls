"""Bounded, action-specific model views. Full runtime state is never mutated.

Character limits are transport guards, NOT token counts or GPU-memory guarantees.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from .two_phase_state import build_policy_state


class ContextLimitError(ValueError):
    pass


def serialize(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def event_view(event):
    """Keep all event IDs/dates and exact summaries; do not cut off factual clauses."""
    row = {k: copy.deepcopy(event[k]) for k in ('event_id', 'time', 'summary') if k in event}
    if event.get('conflict'):
        row['conflict'] = True
    return row


def candidate_view(row):
    return {k: (copy.deepcopy(row[k]) if k != 'event' else
                {n: copy.deepcopy(row[k][n]) for n in ('time', 'summary', 'actors', 'location') if n in row[k]})
            for k in ('candidate_id', 'status', 'event', 'evidence_ids', 'confidence',
                      'relevance_pass', 'contribution_pass') if k in row}


def gap_view(row):
    return {k: copy.deepcopy(row[k]) for k in ('gap_id', 'type', 'description', 'priority', 'status',
            'left_event_id', 'right_event_id', 'temporal_context', 'retrieval_target',
            'deferred_reason') if k in row}


def _tokens(text):
    return set(re.findall(r'[a-z0-9]+', str(text).lower())) - {
        'the', 'and', 'of', 'to', 'a', 'in', 'is', 'was', 'for', 'with', 'on', 'from',
        'event', 'events', 'gap', 'missing', 'needs', 'evidence', 'between', 'this', 'that'}


def memory_view(memory, active_gap=None, *, settings=None, stage=''):
    settings = settings or {}
    exploration = memory.get('exploration', memory)
    history = exploration.get('search_history', exploration.get('recent_searches', []))
    if 'exploration' in memory:
        history = history + memory.get('search_history', [])
    recent = []
    for row in history[-settings.get('recent_queries', 8):]:
        recent.append({k: copy.deepcopy(row[k]) for k in ('query', 'strategy', 'date_filter',
            'new_verified_event_count', 'evidence_update_count', 'returned_documents', 'skipped') if k in row})
    focus = (active_gap or {}).get('description', '') or ' '.join(exploration.get('next_search_directions', []))
    target = _tokens(focus)
    leads = exploration.get('lead_queue', exploration.get('provisional_leads', exploration.get('pending_leads', [])))
    selected, seen = [], set()
    ranked = sorted(enumerate(leads), key=lambda pair: (
        -len(target & _tokens(pair[1].get('summary', ''))),
        -float(pair[1].get('priority', 0)), pair[0]))
    for _, row in ranked:
        if row.get('status') not in {None, 'OPEN', 'INSUFFICIENT'}:
            continue
        key = ' '.join(str(row.get('summary', '')).casefold().split())
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append({k: copy.deepcopy(row[k]) for k in ('lead_id', 'time', 'summary', 'status') if k in row})
        if len(selected) >= settings.get('pending_leads', 8):
            break
    stages = [{k: copy.deepcopy(row[k]) for k in ('period', 'description') if k in row}
              for row in exploration.get('stage_outline', [])[:12]]
    result = {'stage_outline': stages, 'recent_searches': recent, 'pending_leads': selected,
        'unresolved_lead_count': exploration.get('unresolved_lead_count', len(leads)),
        'search_history_count': exploration.get('search_history_count', len(history)),
        'discovered_keywords': list(dict.fromkeys(exploration.get('discovered_keywords', [])))[:12]}
    for key in ('skeleton_ready', 'boundary_assessment', 'observed_date_range'):
        if key in exploration:
            result[key] = copy.deepcopy(exploration[key])
    result['next_search_directions'] = copy.deepcopy(exploration.get('next_search_directions', [])[:8])
    result['deferred_directions'] = copy.deepcopy(exploration.get('deferred_directions', [])[:8])
    if 'MEMORY_UPDATE' in stage:
        # The memory validator requires provenance for stage updates, not whole source bodies.
        result['stage_outline'] = [{k: copy.deepcopy(row[k]) for k in ('period', 'description', 'evidence_ids') if k in row}
                                   for row in exploration.get('stage_outline', [])[:12]]
        result['current_evidence_ids'] = copy.deepcopy(exploration.get('current_evidence_ids',
            list(history[-1].get('result_ids', [])) if history else []))
    gaps = memory.get('gaps', [])
    if gaps:
        result['gap_status_counts'] = dict(Counter(g.get('status', 'OPEN') for g in gaps))
        result['known_gap_ids'] = [g['gap_id'] for g in gaps]
        if 'GAP_MEMORY' in stage:
            open_gaps = [g for g in gaps if g.get('status') in {'OPEN', 'IN_PROGRESS', 'FAILED'}]
            open_gaps.sort(key=lambda g: (g.get('gap_id') != (active_gap or {}).get('gap_id'), -float(g.get('priority', 0))))
            result['related_gaps'] = [gap_view(g) for g in open_gaps[:settings.get('related_gaps', 6)]]
            result['omitted_open_gap_count'] = max(0, len(open_gaps) - len(result['related_gaps']))
    return result


def observation_view(observation, settings=None):
    settings = settings or {}
    result = {}
    for key in ('query', 'retrieval_parameters'):
        if key in observation:
            result[key] = copy.deepcopy(observation[key])
    if 'retrieved_documents' in observation:
        result['retrieved_documents'] = [{k: copy.deepcopy(d[k]) for k in ('id', 'title', 'text', 'context_before') if k in d}
                                          for d in observation['retrieved_documents']]
        for projected, original in zip(result['retrieved_documents'], observation['retrieved_documents']):
            if original.get('temporal_annotations'):
                projected['temporal_annotations'] = [{k: a[k] for k in ('annotation_id', 'expression', 'date', 'rule')}
                                                     for a in original['temporal_annotations'][:12]]
                for compact, annotation in zip(projected['temporal_annotations'], original['temporal_annotations']):
                    if annotation.get('source_quote'):
                        compact['source_quote'] = annotation['source_quote']
                    if annotation.get('year_anchor'):
                        compact['year_context'] = annotation['year_anchor']['quote']
                projected['publication_anchor'] = original.get('publication_date')
    for key in ('extracted_candidates', 'all_extracted_candidates', 'verified_candidates'):
        if key in observation:
            rows = observation[key]
            limit = settings.get('observed_candidates', 16)
            result[key] = [candidate_view(r) for r in rows[:limit]]
            result[key + '_omitted_count'] = max(0, len(rows) - limit)
    for key in ('applied', 'merge_operations'):
        if key in observation:
            result[key] = copy.deepcopy(observation[key])
    return result


def deduplicate_gap_observation(observation):
    """Remove only records that can be reconstructed exactly from another field."""
    result = copy.deepcopy(observation)
    extracted = result.get('all_extracted_candidates', [])
    verified = result.get('verified_candidates', [])
    if 'all_extracted_candidates' in result and 'verified_candidates' in result:
        by_id = {r['candidate_id']: r for r in extracted}
        if len(by_id) == len(extracted) and all(by_id.get(r['candidate_id']) == r for r in verified):
            result['verified_candidate_ids'] = [r['candidate_id'] for r in verified]
            del result['verified_candidates']
    applied = result.get('applied', [])
    operations = result.get('merge_operations', [])
    if operations and all(any(all(k in a and a[k] == v for k, v in op.items()) for a in applied) for op in operations):
        del result['merge_operations']
        result['merge_operations_recorded_in_applied'] = True
    return result


def compact_gap_update(projected):
    """Apply the same loss-checked transformation to runtime and recorded packets."""
    result = copy.deepcopy(projected)
    if 'update GAP_MEMORY' not in result.get('stage', ''):
        return result
    if 'cycle_observation' in result:
        result['cycle_observation'] = deduplicate_gap_observation(result['cycle_observation'])
    if isinstance(result.get('active_gap'), dict):
        visible_gap = result.get('student_visible_state', {}).get('active_gap', {})
        if visible_gap.get('gap_id') and visible_gap['gap_id'] == result['active_gap'].get('gap_id'):
            result['active_gap'] = gap_view(result['active_gap'])
    if 'verified_candidate_ids' in result.get('cycle_observation', {}):
        hint = (' verified_candidate_ids reference identical records in all_extracted_candidates. '
                'Applied operations preserve the merge decisions; omitted-count fields still apply.')
        if hint not in result.get('context_rule', ''):
            result['context_rule'] = result.get('context_rule', '') + hint
    return result


def state_view(state, stage='', settings=None):
    settings = settings or {}
    result = {k: copy.deepcopy(state[k]) for k in ('schema_version', 'dataset', 'topic', 'phase',
                                                   'keywords', 'valid_actions') if k in state}
    events = state.get('events', [])
    result['events'] = sorted((event_view(e) for e in events), key=lambda e: (str(e.get('time', '')), e.get('event_id', '')))
    memory = state.get('memory', {})
    if stage == 'VERIFY':
        result['events'] = []
        result['memory'] = {'already_extracted': copy.deepcopy(memory.get('already_extracted', []))}
    elif stage.startswith('UPDATE:'):
        result['events'] = []  # Exactly two unabridged input events are supplied separately.
        result['memory'] = {}
    elif stage.startswith('MERGE:'):
        result['memory'] = {}
    else:
        result['memory'] = memory_view(memory, state.get('active_gap'), settings=settings, stage=stage)
        if 'MEMORY_UPDATE' not in stage:
            if state.get('active_gap') and ('SEARCH' in stage or 'update GAP_MEMORY' in stage):
                purpose = 'repair_gap'
            elif 'GAP_MEMORY' in stage:
                purpose = 'discover_gaps'
            elif state.get('phase') == 'GAP_REFINEMENT':
                purpose = 'stop'
            else:
                purpose = 'explore'
            result = build_policy_state(state, purpose=purpose,
                local_event_limit=settings.get('local_events', 16),
                recent_query_limit=settings.get('recent_queries', 8))
    if state.get('active_gap'):
        result['active_gap'] = gap_view(state['active_gap'])
    if 'retrieval_contract' in state:
        # Events already contain the exact ID/date mapping; do not repeat every anchor.
        result['retrieval_contract'] = {k: copy.deepcopy(v) for k, v in state['retrieval_contract'].items()
                                        if k != 'visible_anchors'}
    if 'tool_observation' in state:
        result['tool_observation'] = observation_view(state['tool_observation'], settings)
    return result


CONTEXT_RULE = (
    'State is a compact view, not the complete archive. Omitted leads/gaps are not resolved. '
    'events retain exact ID/date/summary for visible nodes; local repairs omit unrelated nodes. '
    'pending_leads are unverified. Recent queries are a subset, not a deduplication guarantee. '
    'Explore stages, rough development, boundaries and unexplored branches before detailed gap repair. '
    'Gap discovery checks missing branches and incomplete events, not only adjacent dates. '
    'No results is unresolved; an empty query skips one gap, not the entire task. '
    'For MEMORY_UPDATE cite exact known passage IDs or visible verified event IDs resolved through stored provenance. '
    'Return only the required JSON and a short decision reason.'
)


def compact_instruction(instruction, settings=None):
    settings = settings or {}
    def check_private(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (str(key).startswith('teacher_only_') or key in {
                    'private_reference_events', 'gold_events', 'gold_timeline', 'teacher_alignment'}) and child:
                    raise ValueError('Compact policy input contains private reference fields')
                check_private(child)
        elif isinstance(value, list):
            for child in value:
                check_private(child)
    check_private(instruction)
    if instruction.get('protocol') == 'batch-v9':
        return copy.deepcopy(instruction)
    stage = instruction.get('stage', '')
    result = copy.deepcopy(instruction)
    for key in list(result):
        if key.startswith('teacher_only_'):
            if result[key]:
                raise ValueError('Compact deployed controller must not receive private teacher references')
            result.pop(key)
    for key in ('student_visible_state', 'current_timeline_state', 'state_before_verify'):
        if key in result:
            result[key] = state_view(result[key], stage, settings)
    if 'cycle_observation' in result:
        result['cycle_observation'] = observation_view(result['cycle_observation'], settings)
    if 'verified_candidates' in result:
        result['verified_candidates'] = [candidate_view(r) for r in result['verified_candidates']]
    if 'retrieved_documents' in result:
        result['retrieved_documents'] = observation_view({'retrieved_documents': result['retrieved_documents']})['retrieved_documents']
    if stage == 'FINAL_SELECT' and 'events' in result:
        result['events'] = [event_view(e) for e in result['events']]
    if 'repair' in result:
        # Repairs must still contain the complete previous output, not the full old state.
        result['repair'] = copy.deepcopy(instruction['repair'])
    result['context_rule'] = CONTEXT_RULE
    return compact_gap_update(result)


class CompactContextClient:
    """Project before cache/network; capture exact accepted controller training messages."""
    def __init__(self, client, settings=None):
        self.client = client
        self.model = client.model
        self.settings = settings or {}
        for key in ('recent_queries', 'pending_leads', 'related_gaps', 'observed_candidates',
                    'local_events', 'max_request_chars'):
            if key in self.settings and (type(self.settings[key]) is not int or self.settings[key] < 1):
                raise ValueError(f'{key} must be a positive integer')
        self.accepted = []
        self.last_exchange = None
        self.projections = []

    def chat(self, messages, temperature=0.0):
        self.last_exchange = None
        if len(messages) != 2 or messages[1]['role'] != 'user':
            raise ValueError('Compact controller expects one system message and one structured user input')
        raw = json.loads(messages[1]['content'])
        compact = compact_instruction(raw, self.settings)
        projected = [copy.deepcopy(messages[0]), {'role': 'user', 'content': serialize(compact)}]
        chars = sum(len(m['content']) for m in projected)
        limit = self.settings.get('max_request_chars', 64000)
        if chars > limit:
            raise ContextLimitError(f'Compact request has {chars} characters, exceeding {limit}; no HTTP call made; do not silently truncate events')
        record = {'stage': raw.get('stage'), 'before_chars': sum(len(m['content']) for m in messages), 'after_chars': chars}
        self.projections.append(record)
        self.last_exchange = None
        response = self.client.chat(projected, temperature=temperature)
        self.last_exchange = {'messages': projected, 'response': response.text,
                              'stage': raw.get('stage'), 'is_repair': 'repair' in raw}
        return response

    def accept_last_response(self, checked):
        if hasattr(self.client, 'accept_last_response'):
            self.client.accept_last_response(checked)
        if not self.last_exchange:
            return
        output = checked.get('student_output', checked)
        if output.get('action') not in {'SEARCH', 'STOP', 'GAP_MEMORY', 'MEMORY_UPDATE'}:
            self.last_exchange = None
            return
        exchange = self.last_exchange
        if json.loads(exchange['messages'][-1]['content']).get('protocol') == 'batch-v9' and exchange['stage'] != 'BATCH_POLICY':
            self.last_exchange = None
            return
        identity = hashlib.sha256(serialize(exchange['messages']).encode()).hexdigest()
        self.accepted.append({'request_sha256': identity,
            'messages': copy.deepcopy(exchange['messages']) + [{'role': 'assistant', 'content': exchange['response']}],
            'metadata': {'stage': exchange['stage'], 'action': output['action'], 'is_repair': exchange['is_repair'],
                         'structurally_validated': True, 'training_ready': False, 'split': 'unassigned',
                         'exact_inference_messages': True, 'semantic_review_required': True}})
        self.last_exchange = None

    def statistics(self):
        result = self.client.statistics() if hasattr(self.client, 'statistics') else {}
        return {**result, 'context_projections': copy.deepcopy(self.projections),
                'accepted_controller_samples': len(self.accepted)}


def dpo_pair(chosen_row, rejected_row):
    """Format only; the caller still needs an independently justified preference label."""
    left, right = chosen_row['messages'], rejected_row['messages']
    if left[:-1] != right[:-1]:
        raise ValueError('DPO chosen/rejected must share the exact same prompt')
    if left[-1]['role'] != 'assistant' or right[-1]['role'] != 'assistant':
        raise ValueError('DPO completions must be assistant messages')
    if left[-1]['content'] == right[-1]['content']:
        raise ValueError('DPO needs different completions')
    return {'prompt': copy.deepcopy(left[:-1]), 'chosen': [copy.deepcopy(left[-1])],
            'rejected': [copy.deepcopy(right[-1])],
            'metadata': {'preference_verified': False, 'training_ready': False}}
