"""First-phase event retrieval mode for the existing two-phase CLI.

Reuses article ranking, SEARCH policy, factual-memory compression, API caching,
request limits and token budgets. VERIFY classifies immutable extraction rows.
Reference timelines are loaded only after the rollout for offline evaluation.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import batched_search_phase as controller
import llm_tls_network_pause as network
from chronos_repro import batch_memory
from chronos_repro.atomic_io import atomic_write_json
from chronos_repro.data import load_timelines
from chronos_repro.envfile import load_env_file
from chronos_repro.limited_llm import LimitedDeepSeekClient
from chronos_repro.llm import InsufficientBalanceError
from chronos_repro.llm_cache import CachedLLMClient
from chronos_repro.preextracted_events import (
    ExtractedEventStore, VERIFY_SYSTEM, candidates_from_documents, local_events,
    verify_instruction, validate_decisions, recover_decisions, apply_decisions,
)
from chronos_repro.retrieval import _bm25_path
from chronos_repro.run_guard import source_binding
from chronos_repro.snapshot import sha256
from chronos_repro.tisa_rollout import SKELETON
from chronos_repro.token_budget import TokenBudgetClient
from run_tisa_two_phase_annotation import repaired_call, add_audits, model_step, LabelValidationError


def now():
    return datetime.now(timezone.utc).isoformat()


def checkpoint(output, state, trajectory, usage):
    saved = {k: copy.deepcopy(v) for k, v in state.items() if not k.startswith('_')}
    atomic_write_json(output / 'checkpoint.json', {
        'updated_at_utc': now(), 'state': saved, 'trajectory': trajectory, 'usage': usage})


def evaluate_dates_only(config, project, state, store):
    # No caller reads reference dates until SEARCH and VERIFY have ended.
    path = project / config['data'] / config['topic'] / 'timelines.jsonl'
    gold = {d.isoformat() for timeline in load_timelines(path) for d in timeline}
    def days(events, key):
        result = set()
        for event in events:
            value = event.get(key)
            if isinstance(value, str) and len(value) == 10:
                date.fromisoformat(value)
                result.add(value)
        return result
    accepted = days(state['timeline_events'], 'time')
    retrieved = days([e for doc_id in state.get('retrieved_document_ids', [])
                      for e in store.articles[doc_id]['events']], 'event_date')
    available = days([e for row in store.articles.values() for e in row['events']], 'event_date')
    begin, end = (min(accepted), max(accepted)) if accepted else (None, None)
    hits = gold & accepted
    return {'metric': 'exact_gold_date_coverage', 'gold_date_count': len(gold),
            'matched_gold_date_count': len(hits), 'gold_date_recall': len(hits) / len(gold),
            'matched_gold_dates': sorted(hits), 'missed_gold_dates': sorted(gold - hits),
            'predicted_date_count': len(accepted), 'predicted_start': begin, 'predicted_end': end,
            'gold_start': min(gold), 'gold_end': max(gold),
            'gold_start_exactly_matched': min(gold) in accepted,
            'gold_end_exactly_matched': max(gold) in accepted,
            'span_contains_gold_endpoints': bool(begin and begin <= min(gold) and end >= max(gold)),
            'retrieved_extracted_date_hits': len(gold & retrieved),
            'all_topic_extracted_date_hits': len(gold & available),
            'not_available_in_extraction': sorted(gold - available),
            'available_but_not_retrieved': sorted((gold & available) - retrieved),
            'retrieved_but_not_selected': sorted((gold & retrieved) - accepted),
            'partial_or_unknown_selected_events': sum(e.get('date_precision') != 'day' for e in state['timeline_events']),
            'gold_used_in_search_or_verify': False, 'gold_sha256': sha256(path),
            'limitations': ['Date matches do not establish event semantic correctness.',
                           'Diagnostic topics were selected by historical Gold performance.',
                           'Historical runs differ in budgets and versions; not a controlled equal-cost comparison.']}


def finish_batch(client, state, config, trajectory, usage, output):
    batch = state['pending_batch']
    processed = set(state.get('reviewed_candidate_ids', []))
    remaining = [c for c in batch['candidates'] if c['candidate_id'] not in processed]
    while remaining:
        if (output / 'STOP').exists():
            raise network.NetworkPauseStopped('User stop; unfinished candidates remain checkpointed')
        size = min(config['preextracted_events'].get('verify_batch_events', 8), len(remaining))
        while True:
            candidates = remaining[:size]
            context = local_events(state['timeline_events'], candidates,
                                   config['preextracted_events'].get('merge_context_events', 8))
            payload = verify_instruction(state['topic'], candidates, context)
            if controller._fits(client, VERIFY_SYSTEM, payload):
                break
            if size == 1:
                raise ValueError('One extracted event exceeds VERIFY input budget; no truncation applied')
            size -= 1
        recovery = None
        try:
            decision, audits = repaired_call(client, VERIFY_SYSTEM, payload,
                lambda p: validate_decisions(p, candidates, context), config)
        except LabelValidationError as error:
            decision, recovery = recover_decisions(error.payload, candidates, context)
            audits = error.audits
        add_audits(trajectory, usage, SKELETON + ':VERIFY_PREEXTRACTED', audits)
        applied = apply_decisions(state, candidates, decision['decisions'])
        decided_ids = {r['candidate_id'] for r in decision['decisions']}
        processed.update(decided_ids)
        deferred = state.setdefault('deferred_verification', {})
        for cid in decided_ids:
            deferred.pop(cid, None)
        for failure in (recovery or {}).get('deferred', []):
            cid = failure['candidate_id']
            candidate = next(c for c in candidates if c['candidate_id'] == cid)
            deferred[cid] = {**copy.deepcopy(candidate), **failure, 'status': 'VERIFY_DEFERRED',
                'attempts': deferred.get(cid, {}).get('attempts', 0) + 1,
                'reason_scope': 'local_decision_validation_not_missing_source_evidence'}
        state['reviewed_candidate_ids'] = sorted(processed)
        batch_memory.sync_events(state)
        model_step(trajectory, SKELETON, 'VERIFY', payload, decision,
                   {'applied': applied, 'fact_check_performed': False,
                    'summary_and_time_rewritten': False,
                    **({'executor_recovery': recovery, 'training_target': False} if recovery else {})},
                   'executor_recovery_not_training_target' if recovery else 'preextracted_relevance_membership')
        checkpoint(output, state, trajectory, usage)
        remaining = remaining[size:]
    memory = batch_memory.initialize(state)
    changes = [r for r in memory['pending_changes'] if r['revision'] > batch['before_revision']]
    memory['batches_since_summary'] += 1
    state.setdefault('query_batches', []).append({
        'batch_id': batch['batch_id'], 'phase': SKELETON, 'query_count': len(batch['queries']),
        'before_revision': batch['before_revision'], 'after_revision': memory['revision'],
        'event_change_count': len(changes),
        'appended_event_count': sum(r['operation'] == 'APPEND' for r in changes),
        'updated_event_count': sum(r['operation'] == 'UPDATE' for r in changes),
        'deleted_event_count': 0})
    for row in memory['query_history'][-len(batch['queries']):]:
        row.update(batch_event_change_count=len(changes), gain_attribution='joint_batch_not_individual_query_gain')
    state['retrieval_feedback'] = {'last_batch_articles': len(batch['documents']),
        'articles_without_events': sum(not d['events'] for d in batch['documents']),
        'candidate_events': len(batch['candidates']), 'event_changes': len(changes),
        'unread_candidate_events': 0, 'raw_article_returned': False,
        'deferred_verification_count': len(state.get('deferred_verification', {})),
        'deferred_verification_scope': 'Local decision failures, not missing source evidence; retained for review.'}
    del state['pending_batch']
    checkpoint(output, state, trajectory, usage)


def rollout(client, state, config, store, index, trajectory, usage, output):
    if state.get('pending_batch'):
        finish_batch(client, state, config, trajectory, usage, output)
    for _ in range(len(state.get('query_batches', [])), config['phase1_max_rounds']):
        if (output / 'STOP').exists():
            raise network.NetworkPauseStopped('User stopped phase one')
        decision, visible = controller.decide(client, state, config, trajectory, usage, SKELETON)
        if decision['action'] == 'STOP':
            forced = trajectory['steps'][-1]['observation'].get('forced', False)
            state['phase1_termination'] = 'invalid_policy_handoff' if forced else 'autonomous_stop'
            controller.refresh_summary(client, state, config, trajectory, usage, force=True)
            return
        batch_id = len(state.get('query_batches', [])) + 1
        documents, queries = {}, []
        for query in decision['queries']:
            filt = query['time_filter']
            docs = store.retrieve(index, query['query'], config['top_k'],
                date_from=filt['start'], date_to=filt['end'], date_filter_mode=filt['mode'],
                date_soft_penalty=config['temporal_search'].get('soft_penalty', 0.5))
            record = {**copy.deepcopy(query), 'batch_id': batch_id, 'gap_id': None,
                      'result_ids': [d['document_id'] for d in docs]}
            queries.append(record)
            batch_memory.record_query(state, record)
            state['search_history'].append(copy.deepcopy(record))
            documents.update({d['document_id']: d for d in docs})
        state['retrieved_document_ids'] = sorted(set(state.get('retrieved_document_ids', [])) | set(documents))
        candidates = candidates_from_documents(list(documents.values()))
        state['pending_batch'] = {'batch_id': batch_id, 'queries': queries,
            'documents': list(documents.values()), 'candidates': candidates,
            'before_revision': batch_memory.initialize(state)['revision']}
        model_step(trajectory, SKELETON, 'BATCH_RETRIEVAL', visible, {'batch_id': batch_id},
                   {'queries': queries, 'documents': list(documents.values()),
                    'raw_article_returned': False}, 'article_ranking_extracted_events_return')
        checkpoint(output, state, trajectory, usage)
        finish_batch(client, state, config, trajectory, usage, output)
    state['phase1_termination'] = 'runner_limit'
    controller._forced_stop(state, trajectory, SKELETON)
    controller.refresh_summary(client, state, config, trajectory, usage, force=True)


def execute_locked(args, project, config_path, config, output):
    if not config.get('first_phase_only') or config.get('phase1_baseline') or config.get('phase1_supplement', {}).get('enabled'):
        raise ValueError('Pre-extracted mode currently requires a fresh first-phase-only configuration')
    keywords = config.get('keywords', [config['topic'].replace('_', ' ')])
    if (not isinstance(keywords, list) or not 1 <= len(keywords) <= 12
            or any(not isinstance(value, str) or not value.strip() for value in keywords)):
        raise ValueError('keywords must contain 1-12 nonempty strings')
    settings = config['preextracted_events']
    path = project / settings['event_root'] / config['dataset'] / (config['topic'] + '_events.jsonl')
    store = ExtractedEventStore(path, config['dataset'], config['topic'], Path(config['data']).name)
    index = project / config['index']
    with sqlite3.connect(_bm25_path(index).resolve().as_uri() + '?mode=ro', uri=True) as con:
        article_rows = con.execute('select doc_id, timestamp from documents where topic=?', (config['topic'],)).fetchall()
    store.validate_index_inventory(article_rows)
    article_ids = set(store.articles)
    binding = {'config_sha256': sha256(config_path), 'event_store_sha256': store.sha256,
               'index_manifest_sha256': sha256(index), 'source': source_binding(project),
               'articles': store.source_record_count, 'returned_article_keys': len(article_ids),
               'identical_output_aliases': dict(store.identical_output_aliases)}
    saved_binding = output / 'preextracted_binding.json'
    if saved_binding.exists() and json.loads(saved_binding.read_text(encoding='utf-8')) != binding:
        raise ValueError('Inputs or runtime changed; use a new run directory')
    atomic_write_json(saved_binding, binding)
    atomic_write_json(output / 'run_config.json', config)
    atomic_write_json(output / 'preflight.json', {'articles': len(article_ids), 'article_ids_match': True,
        'returned_content': 'extracted_events_only', 'first_phase_only': True, 'api_calls': 0})
    if args.dry_run:
        print(json.dumps({'articles': len(article_ids), 'preflight': 'passed', 'api_calls': 0}), flush=True)
        return 0
    if not args.allow_api:
        raise ValueError('API authorization flag required')
    if (output / 'trajectory.json').exists():
        previous = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
        if previous['status'] == 'ok':
            print('Run already finished; no new API requests.', flush=True)
            return 0
    checkpoint_path = output / 'checkpoint.json'
    if checkpoint_path.exists():
        saved = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        state, trajectory, usage = saved['state'], saved['trajectory'], saved['usage']
    else:
        state = {'dataset': config['dataset'], 'topic': config['topic'], 'phase': SKELETON,
                 'timeline_events': [], 'candidate_pool': [], 'search_history': [], 'query_batches': [],
                 'keywords': list(keywords), 'exploration_memory': {}}
        trajectory = {'dataset': config['dataset'], 'topic': config['topic'],
                      'pipeline_revision': config['pipeline_revision'], 'steps': [], 'audits': [],
                      'first_phase_only': True, 'started_at_utc': now()}
        usage = {k: 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'logical_calls', 'http_attempts')}
    state['_preextracted_events'] = True
    state['_checkpoint_hook'] = lambda s, t: checkpoint(output, s, t, usage)
    network.configure_direct_transport()
    load_env_file(args.env_file)
    gate = network.NetworkGate(output, config.get('base_url', 'https://api.deepseek.com'))
    raw = network.guarded_client_class(gate, LimitedDeepSeekClient)(
        model=config['model'], base_url=config.get('base_url', 'https://api.deepseek.com'),
        timeout=90, max_retries=2, request_limit=config['max_api_requests'],
        request_ledger_path=output / 'request_ledger.json', unlimited_verify=config.get('unlimited_verify', True),
        request_options={'thinking': {'type': 'disabled'}})
    token_settings = {**config['batch_controller']['tokens']}
    token_settings['tokenizer_path'] = str(project / token_settings['tokenizer_path'])
    client = TokenBudgetClient(CachedLLMClient(raw, output / 'api_cache'), token_settings)
    status, error = 'ok', None
    try:
        if not state.get('phase1_termination'):
            rollout(client, state, config, store, index, trajectory, usage, output)
    except Exception as exc:
        status, error = 'stopped_error', type(exc).__name__ + ': ' + str(exc)
        if isinstance(exc, LabelValidationError):
            add_audits(trajectory, usage, 'FAILED_LABEL_VALIDATION', exc.audits)
        if isinstance(exc, InsufficientBalanceError):
            status = 'stopped_insufficient_balance'
        print(error, flush=True)
    finally:
        gate.cancel('phase1_runner_exited')
        checkpoint(output, state, trajectory, usage)
    trajectory.update(status=status, error=error, usage=usage, finished_at_utc=now(),
        phase1_termination=state.get('phase1_termination'), phase2_termination='not_requested_first_phase_only',
        final_events=state['timeline_events'], query_batches=state.get('query_batches', []),
        request_accounting=raw.ledger, final_exploration_memory=state['exploration_memory'])
    atomic_write_json(output / 'trajectory.json', trajectory)
    atomic_write_json(output / 'candidate_pool.json', state['candidate_pool'])
    atomic_write_json(output / 'deferred_verification.json', state.get('deferred_verification', {}))
    atomic_write_json(output / 'event_pool.json', state['timeline_events'])
    token_audit = client.statistics()
    atomic_write_json(output / 'token_usage_audit.json', token_audit)
    prediction = {}
    for event in state['timeline_events']:
        if event['date_precision'] == 'day':
            prediction.setdefault(event['time'], []).append(event['summary'])
    atomic_write_json(output / 'prediction.json', prediction)
    evaluation = evaluate_dates_only(config, project, state, store)
    evaluation.update(runtime_status=status, phase1_termination=state.get('phase1_termination'),
        accepted_event_count=len(state['timeline_events']), candidate_count=len(state['candidate_pool']),
        deferred_verification_count=len(state.get('deferred_verification', {})),
        query_count=len(state['search_history']), completed_batches=len(state.get('query_batches', [])),
        retrieved_articles=len(state.get('retrieved_document_ids', [])), usage=usage,
        returned_response_usage_cumulative=token_audit['client']['all_cached_response_usage'],
        candidate_status_counts=dict(Counter(c['status'] for c in state['candidate_pool'])))
    atomic_write_json(output / 'gold_date_coverage.json', evaluation)
    print(json.dumps({k: evaluation[k] for k in ('runtime_status', 'gold_date_recall',
        'matched_gold_date_count', 'gold_date_count', 'accepted_event_count', 'query_count')}, ensure_ascii=False), flush=True)
    return 0 if status == 'ok' else 3 if status == 'stopped_insufficient_balance' else 2
