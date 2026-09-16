"""Coverage-oriented execution, reusing the existing model tools and audit format."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from chronos_repro.evidence_access import EvidenceReader, related_events
from chronos_repro.exploration_memory import (apply_memory_output, validate_memory_output,
                                             validate_exploration_policy, skeleton_stop_allowed)
from chronos_repro.exploration_control import record_cycle_outcome
from chronos_repro.tisa_rollout import SKELETON, validate_student_state, validate_thought
from chronos_repro.retrieval import search
from chronos_repro.label_repair import recover_boundary_summary


def enabled(config):
    return bool(config.get('coverage_pipeline', {}).get('enabled'))


def reader_for(state, index, config):
    if '_evidence_reader' not in state:
        settings = {**config['coverage_pipeline']}
        if state.get('_frozen_topic_path'):
            settings['frozen_topic_path'] = state['_frozen_topic_path']
        state['_evidence_reader'] = EvidenceReader(index, state['topic'], settings)
    return state['_evidence_reader']


def retrieve_passages(state, config, index, query, results, temporal=None, passage_limit=None):
    reader = reader_for(state, index, config)
    reader.add_results(results)
    docs = reader.select(query, limit=passage_limit, events=state['timeline_events'], temporal=temporal)
    current_ids = {str(r['id']) for r in results}
    for doc in docs:
        doc['reader_origin'] = 'current_results' if doc['document_id'] in current_ids else 'historical_reuse'
    state['evidence_progress'] = reader.summary()
    return docs


def _compact_memory(memory):
    return {k: copy.deepcopy(v) for k, v in memory.items()
            if k not in {'observed_events', 'search_history', 'exploration'}} | {
                'recent_searches': copy.deepcopy(memory.get('search_history', [])[-8:])}


def refresh_evidence_memory(state):
    """Persist model-extracted leads without re-labelling them as verified facts."""
    memory = state.setdefault('exploration_memory', {})
    pool = state.get('candidate_pool', [])
    observations, leads = {}, {}
    old_leads = {r['lead_id']: r for r in memory.get('lead_queue', [])}
    keywords = list(memory.get('discovered_keywords', []))
    passages = getattr(state.get('_evidence_reader'), 'passages', {})
    for row in pool:
        if not row.get('relevance_pass'):
            continue
        event = row['event']
        key = (event.get('time'), ' '.join(event['summary'].casefold().split()))
        observation_id = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
        observations[observation_id] = {
            'observation_id': observation_id, 'event_time': event.get('time'),
            'summary': event['summary'], 'document_id': row['evidence_ids'][0],
            'evidence_ids': row['evidence_ids'], 'status': row['status']}
        source = ' '.join(str(passages.get(i, {}).get('text', '')) for i in row['evidence_ids']).casefold()
        for actor in event.get('actors', []):
            if isinstance(actor, str) and actor.strip() and actor.casefold() in source and actor not in keywords:
                keywords.append(actor)
        if row['status'] == 'INSUFFICIENT':
            previous = old_leads.get(observation_id, {})
            leads[observation_id] = {
                'lead_id': observation_id, 'time': event.get('time'), 'summary': event['summary'],
                'evidence_ids': row['evidence_ids'], 'status': previous.get('status', 'OPEN'),
                'priority': 1.0 if row.get('contribution_pass') else 0.5,
                'attempted_queries': previous.get('attempted_queries', []),
                'resolution_event_ids': previous.get('resolution_event_ids', [])}
    # Only exact factual identity closes a lead automatically; similarity is not entailment.
    for lead in leads.values():
        matches = [e['event_id'] for e in state['timeline_events']
                   if ' '.join(e['summary'].casefold().split()) == ' '.join(lead['summary'].casefold().split())
                   and (not lead.get('time') or e['time'] == lead['time']
                        or e['time'].startswith(str(lead['time']) + '-'))]
        if matches:
            lead.update(status='RESOLVED', resolution_event_ids=matches)
    memory['lead_queue'] = sorted(leads.values(), key=lambda r: (r['status'] != 'OPEN', -r['priority'], r['lead_id']))
    memory['provisional_leads'] = [r for r in memory['lead_queue'] if r['status'] == 'OPEN'][:24]
    memory['unresolved_lead_count'] = sum(r['status'] == 'OPEN' for r in memory['lead_queue'])
    memory['observed_events'] = sorted(observations.values(), key=lambda r: (r['event_time'] or '9999', r['observation_id']))
    dates = [e['time'] for e in state['timeline_events']]
    memory['observed_date_range'] = {'from': min(dates), 'to': max(dates)} if dates else None
    memory['discovered_keywords'] = keywords


def process_passages(client, state, docs, config, trajectory, usage, phase, visible):
    from run_tisa_two_phase_annotation import verify_batch, execute_merge, model_step, add_audits
    settings = config['coverage_pipeline']
    batch_size = settings.get('verification_batch_passages', 2)
    if batch_size < 1:
        raise ValueError('verification_batch_passages must be positive')
    max_candidates = min(settings.get('candidates_per_batch', 24), 12)
    all_candidates, all_applied, all_operations = [], [], []
    state.setdefault('candidate_pool', [])
    state.setdefault('_extraction_cache', {})
    for offset in range(0, len(docs), batch_size):
        batch = docs[offset:offset + batch_size]
        batch_ids = {d['id'] for d in batch}
        previously_extracted = [{'time': r['event']['time'], 'summary': r['event']['summary']}
                                for r in state['candidate_pool'] if r.get('merge_processed')
                                and set(r.get('source_passage_ids', [])) & batch_ids]
        complete = False
        page_limit = 1 if phase == SKELETON else min(settings.get('extraction_pages', 3), 2)
        for page in range(page_limit):
            # Extraction must not depend on query/Gold/current timeline; novelty is decided at MERGE.
            extract_state = {'dataset': state['dataset'], 'topic': state['topic'], 'phase': phase,
                             'keywords': state.get('keywords', []), 'events': [], 'valid_actions': ['VERIFY'],
                             'memory': {'already_extracted': copy.deepcopy(previously_extracted)}}
            extract_config = {**config, 'max_candidates_per_round': max_candidates, 'coverage_extraction': True}
            extract_config['coarse_extraction'] = phase == SKELETON
            request_key = hashlib.sha256(json.dumps({'documents': batch, 'state': extract_state,
                'model': config.get('model'), 'max_candidates': max_candidates}, sort_keys=True).encode()).hexdigest()
            if request_key in state['_extraction_cache']:
                verified = copy.deepcopy(state['_extraction_cache'][request_key])
                audits = []
            else:
                state['model_visible_state'] = extract_state
                verified, audits = verify_batch(client, state, batch, extract_config)
                state['_extraction_cache'][request_key] = copy.deepcopy(verified)
            add_audits(trajectory, usage, f'{phase}:VERIFY_PASSAGES', audits)
            if verified.get('quarantined_candidates'):
                state.setdefault('verification_quarantine', []).extend(copy.deepcopy(verified['quarantined_candidates']))
            for row in verified['candidates']:
                row['candidate_id'] = f'{request_key[:12]}-p{page}-{row["candidate_id"]}'
                pool_row = copy.deepcopy(row)
                pool_row['source_passage_ids'] = [d['id'] for d in batch if d['id'] in row['evidence_ids']]
                if not any(r['candidate_id'] == row['candidate_id'] for r in state['candidate_pool']):
                    state['candidate_pool'].append(pool_row)
            accepted = [r for r in verified['candidates'] if r['status'] in {'SUPPORTED', 'CONFLICTED'}
                        and r['relevance_pass'] and r['contribution_pass']]
            model_step(trajectory, phase, 'VERIFY', {**extract_state, 'tool_observation': {'retrieved_documents': batch}},
                       verified, {'passed_candidate_ids': [r['candidate_id'] for r in accepted],
                                  'extraction_page': page, 'maximum_reached': len(verified['candidates']) == max_candidates},
                       'partial_validation_quarantine_not_training_target' if verified.get('repair_exhausted')
                       else 'deepseek_actual_rollout_evidence_only')
            merge_visible = copy.deepcopy(visible)
            merge_visible['events'] = related_events(state['timeline_events'], accepted, settings.get('merge_context_events', 40))
            merge_visible['memory'] = _compact_memory(merge_visible.get('memory', {}))
            merge_visible['valid_actions'] = ['MERGE']
            validate_student_state(merge_visible)
            merged, applied = execute_merge(client, state, accepted, config, trajectory, usage, phase, merge_visible)
            if config.get('batch_controller', {}).get('enabled'):
                from chronos_repro.batch_memory import sync_events
                sync_events(state)
            processed_ids = {r['candidate_id'] for r in verified['candidates']}
            for r in state['candidate_pool']:
                if r['candidate_id'] in processed_ids:
                    r['merge_processed'] = True
            all_candidates.extend(verified['candidates'])
            all_applied.extend(applied)
            all_operations.extend(merged['operations'])
            previously_extracted.extend({'time': r['event']['time'], 'summary': r['event']['summary']} for r in verified['candidates'])
            if verified.get('extraction_complete') is True and len(verified['candidates']) < max_candidates:
                complete = True
                break
            if verified.get('repair_exhausted'):
                break  # Keep unresolved extraction recorded; do not repeat the same invalid batch immediately.
        if complete:
            state['_evidence_reader'].mark_processed(batch)
            state['incomplete_extraction'] = [r for r in state.get('incomplete_extraction', [])
                                             if not set(r['passage_ids']).issubset(state['_evidence_reader'].processed)]
        else:
            state.setdefault('incomplete_extraction', []).append({'passage_ids': [d['id'] for d in batch],
                                                                 'reason': 'extraction page limit reached'})
        state['evidence_progress'] = state['_evidence_reader'].summary()
        refresh_evidence_memory(state)
        checkpoint(state, trajectory, usage)
    return all_candidates, {'operations': all_operations}, all_applied


def checkpoint(state, trajectory, usage):
    folder = state.get('_output_dir')
    if not folder:
        return
    from run_full_timeline_api_agent import save_json
    snapshot = {**trajectory, 'status': 'checkpoint', 'usage': copy.deepcopy(usage),
                'final_events': state['timeline_events'], 'candidate_pool': state.get('candidate_pool', []),
                'evidence_progress': state.get('evidence_progress', {}),
                'exploration_memory': state.get('exploration_memory', {}),
                'gap_memory': copy.deepcopy(state.get('gap_memory'))}
    snapshot['verification_quarantine'] = copy.deepcopy(state.get('verification_quarantine', []))
    if 'controller_memory' in state:
        snapshot['controller_memory'] = copy.deepcopy(state['controller_memory'])
        snapshot['query_batches'] = copy.deepcopy(state.get('query_batches', []))
        snapshot['phase1_termination'] = state.get('phase1_termination')
        snapshot['phase2_termination'] = state.get('phase2_termination')
    target = Path(folder) / 'checkpoint.json'
    temporary = target.with_suffix('.tmp')
    save_json(temporary, snapshot)
    from chronos_repro.atomic_io import replace_with_retry
    replace_with_retry(temporary, target)


def run_phase1(client, state, config, index, trajectory, usage):
    from exploration_phase import post_merge_memory_instruction, policy_instruction
    from run_tisa_two_phase_annotation import POLICY_SYSTEM, repaired_call, add_audits, model_step, phase1_visible, LabelValidationError
    state['exploration_memory']['progress_control_enabled'] = True
    for round_index in range(config['phase1_max_rounds'] + 1):
        memory = state['exploration_memory']
        allowed = skeleton_stop_allowed(memory, state['timeline_events'], round_index, config)
        if round_index == config['phase1_max_rounds']:
            state['phase1_termination'] = 'runner_limit'
            model_step(trajectory, SKELETON, 'STOP', phase1_visible(state, ['STOP']),
                       {'thought': 'The safety limit was reached; remaining evidence and gaps stay unresolved.',
                        'action': 'STOP', 'query': '', 'strategy': 'INTERRUPTED'},
                       {'forced': True, 'terminal_for_phase': True}, 'deterministic_runner_limit_not_training_target')
            return
        visible = phase1_visible(state, ['SEARCH', 'STOP'] if allowed else ['SEARCH'])
        visible['memory']['evidence_progress'] = copy.deepcopy(state.get('evidence_progress', {}))
        instruction = policy_instruction(visible)
        instruction['objective'] += (' Coarse readiness is not exhaustive reading. Use known dates and stages to probe '
                                     'under-covered periods. SEARCH also reads relevant unread passages from previously retrieved documents. '
                                     'Never infer missing events solely from a gap between unrelated storylines.')
        try:
            decision, audits = repaired_call(client, POLICY_SYSTEM, instruction,
                                             lambda p: validate_exploration_policy(p, visible), config)
        except LabelValidationError as error:
            from chronos_repro.phase_handoff import can_handoff_repeated_query
            if not can_handoff_repeated_query(error, memory, state['timeline_events'], round_index, config):
                raise
            add_audits(trajectory, usage, f'{SKELETON}:POLICY_STALL', error.audits)
            state['phase1_termination'] = 'policy_stall_handoff'
            model_step(trajectory, SKELETON, 'STOP', visible,
                {'thought': 'Repeated-query repairs were exhausted; transfer the incomplete skeleton to gap refinement.',
                 'action': 'STOP', 'query': '', 'strategy': 'INTERRUPTED'},
                {'forced': True, 'terminal_for_phase': True, 'skeleton_complete': False,
                 'error': str(error), 'raw_invalid_output': error.payload},
                'deterministic_policy_stall_not_training_target')
            checkpoint(state, trajectory, usage)
            return
        add_audits(trajectory, usage, f'{SKELETON}:POLICY', audits)
        if decision['action'] == 'STOP':
            state['phase1_termination'] = 'autonomous_stop'
            model_step(trajectory, SKELETON, 'STOP', visible, decision, {'forced': False, 'terminal_for_phase': True},
                       'deepseek_actual_rollout_no_private_reference')
            return
        query = decision['query']
        results = search(index, [query], config['top_k'], f"{state['dataset']} {state['topic']}")
        docs = retrieve_passages(state, config, index, query, results)
        state['search_history'].append({'query': query, 'strategy': decision['strategy'], 'result_ids': [d['id'] for d in docs]})
        model_step(trajectory, SKELETON, 'SEARCH', visible, decision,
                   {'documents': docs, 'results': results, 'evidence_progress': state['evidence_progress']},
                   'deepseek_actual_rollout_no_private_reference')
        before = copy.deepcopy(state['timeline_events'])
        candidates, _, _ = process_passages(client, state, docs, config, trajectory, usage, SKELETON, phase1_visible(state, ['MERGE']))
        # Record progress BEFORE the model assesses readiness, not after it.
        memory = copy.deepcopy(state['exploration_memory'])
        memory['search_history'].append({'query': query, 'strategy': decision['strategy'],
                                        'result_ids': [d['id'] for d in docs], 'new_observation_count': len(candidates)})
        def source_level(events):
            passages = state['_evidence_reader'].passages
            return [{**e, 'evidence_ids': sorted({passages[i]['document_sha256'] if i in passages else i
                                                  for i in e.get('evidence_ids', [])})} for e in events]
        memory = record_cycle_outcome(memory, source_level(before), source_level(state['timeline_events']))
        memory['evidence_progress'] = state['evidence_progress']
        memory['verified_event_sources'] = {e['event_id']: list(e.get('evidence_ids', [])) for e in state['timeline_events']}
        state['exploration_memory'] = memory
        memory_visible = phase1_visible(state, ['MEMORY_UPDATE'])
        memory_visible['tool_observation'] = {'query': query, 'extracted_candidates': candidates,
                                            'retrieved_documents': [{'id': d['id'], 'title': d['title'], 'text': ''} for d in docs]}
        instruction = post_merge_memory_instruction(memory_visible)
        def validate_outline(p):
            if p.get('observations') != [] or p.get('discovered_keywords') != []:
                raise ValueError('Post-merge outline refresh must not re-extract observations or keywords')
            return validate_memory_output(p, memory, docs)
        recovery = None
        try:
            output, audits = repaired_call(client, POLICY_SYSTEM, instruction, validate_outline, config)
        except LabelValidationError as error:
            try:
                output, recovery = recover_boundary_summary(error.payload, error, validate_outline)
            except (ValueError, KeyError, TypeError):
                raise error
            audits = error.audits
        add_audits(trajectory, usage, f'{SKELETON}:MEMORY_UPDATE', audits)
        # Do not append a second search-history entry for this post-merge memory update.
        after = copy.deepcopy(memory)
        for key in ('stage_outline', 'next_search_directions', 'skeleton_ready', 'boundary_assessment'):
            after[key] = copy.deepcopy(output[key])
        old_deferred = {r['direction']: r for r in memory.get('deferred_directions', [])}
        old_deferred.update({r['direction']: r for r in output.get('deferred_directions', [])})
        after['deferred_directions'] = [r for d, r in old_deferred.items() if d not in after['next_search_directions']]
        state['exploration_memory'] = after
        refresh_evidence_memory(state)
        after = state['exploration_memory']
        model_step(trajectory, SKELETON, 'MEMORY_UPDATE', memory_visible, output,
                   {'memory_after': copy.deepcopy(after), 'transition': 'post_merge_outline_refresh',
                    'boundary_recovery': recovery},
                   'deterministic_boundary_fallback_not_training_target' if recovery
                   else 'deepseek_actual_rollout_no_private_reference')
        checkpoint(state, trajectory, usage)


def finalize(client, state, config, trajectory, usage):
    if config.get('batch_controller', {}).get('enabled'):
        from batched_search_phase import finalize as finalize_batch
        return finalize_batch(client, state, config, trajectory, usage)
    from run_tisa_two_phase_annotation import POLICY_SYSTEM, repaired_call, add_audits, model_step
    state['event_pool'] = copy.deepcopy(state['timeline_events'])
    events = state['event_pool']
    if not events:
        return
    instruction = {'stage': 'FINAL_SELECT', 'topic': state['topic'], 'keywords': state.get('keywords', []),
        'events': events, 'objective': 'Select the final chronological timeline from verified events. Keep broad milestone coverage, '
        'including early, intermediate and later substories; do not enforce uniform dates. Drop only duplicates, irrelevant or '
        'unsupported/uncertain entries. Do not rewrite facts, invent dates, or optimize against private reference events. '
        'There is no target length: retain all distinct relevant milestones. Explain each removal briefly.',
        'required_json': {'thought': 'brief selection rationale', 'keep_event_ids': ['existing IDs'],
                          'drop': [{'event_id': 'existing ID', 'reason': 'specific reason'}]}}
    ids = {e['event_id'] for e in events}
    def validate(payload):
        thought = validate_thought(payload.get('thought'))
        keep, drop = payload.get('keep_event_ids'), payload.get('drop')
        if not isinstance(keep, list) or not isinstance(drop, list):
            raise ValueError('Final selection needs a keep list and explicit drop list')
        dropped = [r['event_id'] for r in drop]
        if len(set(keep + dropped)) != len(keep + dropped) or set(keep + dropped) != ids:
            raise ValueError('Every input event must be kept or dropped exactly once')
        if any(not isinstance(r.get('reason'), str) or len(r['reason'].strip()) < 6 for r in drop):
            raise ValueError('Every removal needs a concrete reason')
        return {'thought': thought, 'keep_event_ids': keep, 'drop': drop}
    output, audits = repaired_call(client, POLICY_SYSTEM, instruction, validate, config)
    add_audits(trajectory, usage, 'FINAL_SELECT', audits)
    state['timeline_events'] = [e for e in events if e['event_id'] in set(output['keep_event_ids'])]
    model_step(trajectory, 'FINALIZATION', 'SELECT', {'events': events, 'memory': {}, 'valid_actions': ['SELECT']}, output,
               {'kept': len(state['timeline_events']), 'removed': len(output['drop'])},
               'general_model_final_selection_not_controller_training')
