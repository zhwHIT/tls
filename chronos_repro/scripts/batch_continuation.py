"""Continue immutable v9 state in a new run directory, retaining cumulative cost."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from chronos_repro.atomic_io import atomic_write_json
from chronos_repro.snapshot import sha256
from chronos_repro.tisa_rollout import SKELETON, REFINE, student_state
from chronos_repro import batch_memory as memory


def prepare(project, output, config):
    spec = config.get('continuation')
    if not spec:
        return None
    path = (project / spec['snapshot']).resolve()
    if not path.is_relative_to(project) or sha256(path) != spec['snapshot_sha256']:
        raise ValueError('Continuation snapshot changed or escapes project')
    parent = path.parent
    saved_config = json.loads((parent / 'run_config.json').read_text(encoding='utf-8'))
    if saved_config.get('phase2_teacher_guidance') is not False:
        raise ValueError('Cannot continue a gold-guided state')
    for key in ('dataset', 'topic', 'data', 'index', 'model'):
        if saved_config[key] != config[key]:
            raise ValueError('Continuation changes bound input: ' + key)
    ledger_path = parent / 'request_ledger.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
    if sha256(ledger_path) != spec['ledger_sha256'] or ledger.get('balance_stop'):
        raise ValueError('Parent ledger changed or account stopped')
    if config['max_api_requests'] != ledger['requests_started'] + spec['additional_requests']:
        raise ValueError('Continuation ceiling must preserve cumulative request count')
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    if snapshot.get('status') in {'ok', 'stopped_insufficient_balance'}:
        raise ValueError('Do not continue an already completed or balance-stopped run')
    if (snapshot['dataset'], snapshot['topic']) != (config['dataset'], config['topic']):
        raise ValueError('Continuation topic mismatch')
    new_ledger = output / 'request_ledger.json'
    if new_ledger.exists():
        raise ValueError('Continuation requires a fresh destination; never reset a ledger')
    atomic_write_json(new_ledger, {'request_limit': config['max_api_requests'],
                                  'requests_started': ledger['requests_started'], 'balance_stop': False})
    cache = output / 'api_cache'
    cache.mkdir(exist_ok=True)
    for entry in (parent / 'api_cache').glob('*.json'):
        shutil.copy2(entry, cache / entry.name)
    lineage = {'parent_snapshot': str(path), 'parent_snapshot_sha256': sha256(path),
               'parent_source_sha256': snapshot.get('source_sha256'),
               'parent_requests': ledger['requests_started'], 'additional_request_ceiling': spec['additional_requests'],
               'mode': 'state_continuation_not_replay_from_start', 'parent_unchanged': True,
               'old_audits_stay_in_parent': True}
    atomic_write_json(output / 'continuation_binding.json', lineage)
    parent_binding = json.loads((parent / 'run_binding.json').read_text(encoding='utf-8'))
    return {'snapshot': snapshot, 'parent': parent, 'lineage': lineage, 'input_binding': parent_binding}


def restore(state, trajectory, continuation, config, index):
    import coverage_pipeline
    old = continuation['snapshot']
    state['timeline_events'] = copy.deepcopy(old['final_events'])
    state['controller_memory'] = copy.deepcopy(old['controller_memory'])
    state['search_history'] = copy.deepcopy(state['controller_memory']['query_history'])
    state['query_batches'] = copy.deepcopy(old.get('query_batches', []))
    state['exploration_memory'] = copy.deepcopy(old.get('final_exploration_memory', old.get('exploration_memory', {})))
    state['gap_memory'] = copy.deepcopy(old.get('final_gap_memory', old.get('gap_memory')) or
                                      {'phase': REFINE, 'gaps': [], 'history': []})
    for key in ('phase1_termination', 'phase2_termination', 'incomplete_extraction', 'verification_quarantine'):
        if key in old:
            state[key] = copy.deepcopy(old[key])
    pool = continuation['parent'] / 'candidate_pool.json'
    state['candidate_pool'] = json.loads(pool.read_text(encoding='utf-8')) if pool.exists() else copy.deepcopy(old.get('candidate_pool', []))
    reader = coverage_pipeline.reader_for(state, index, config)
    manifest_path = continuation['parent'] / 'evidence_reader_manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        reader.passages = {p['id']: p for p in manifest['passages']}
        reader.loaded_documents = {p['document_id'] for p in reader.passages.values()}
        reader.processed = set(manifest.get('processed_passage_ids', []))
    else:
        for row in state['search_history']:
            reader.add_results([{'id': i} for i in row.get('result_ids', [])])
        # Only mark passages explicitly verified as fully extracted.
        for step in old['steps']:
            out = step.get('model_output', {})
            if (step['action'] == 'VERIFY' and out.get('extraction_complete') is True and
                    not step.get('observation', {}).get('maximum_reached') and not out.get('repair_exhausted')):
                ids = [p['id'] for p in step['model_input'].get('tool_observation', {}).get('retrieved_documents', [])]
                reader.processed.update(i for i in ids if i in reader.passages)
    for row in state['search_history']:
        reader.retrieval_ranks.update({str(i): n + 1 for n, i in enumerate(row.get('result_ids', []))})
    last_completed = max((b['batch_id'] for b in state['query_batches']), default=0)
    pending = [r for r in state['search_history'] if r['batch_id'] > last_completed]
    if pending:
        if len({r['batch_id'] for r in pending}) != 1:
            raise ValueError('More than one interrupted batch requires manual reconstruction')
        selected = list(dict.fromkeys(i for r in pending for i in r.get('passage_ids', [])))
        retrieval = max((n for n, s in enumerate(old['steps']) if s['action'] == 'BATCH_RETRIEVAL'), default=-1)
        done, verified_ids = set(), []
        for step in old['steps'][retrieval + 1:]:
            if step['action'] == 'VERIFY':
                verified_ids = [p['id'] for p in step['model_input'].get('tool_observation', {}).get('retrieved_documents', [])]
            elif step['action'] == 'MERGE':
                done.update(verified_ids)
                verified_ids = []
        missing = set(selected) - set(reader.passages)
        if missing:
            raise ValueError('Pending batch passages absent from saved reader')
        state['_pending_continuation_batch'] = {'records': pending,
            'documents': [reader.passages[i] for i in selected if i not in done],
            'before_revision': state['controller_memory']['revision']}
    state['_resumed_phase1_batches'] = count_phase1_batches(state)
    state['evidence_progress'] = reader.summary()
    trajectory['continuation'] = continuation['lineage']
    trajectory['continuation']['restored_event_count'] = len(state['timeline_events'])
    trajectory['continuation']['restored_query_count'] = len(state['search_history'])
    trajectory['initial_state'] = memory.policy_view(state, REFINE if state.get('phase1_termination') else SKELETON)
    return state


def count_phase1_batches(state):
    phase1 = {r['batch_id'] for r in state['search_history'] if not r.get('gap_id')}
    return sum(b['batch_id'] in phase1 for b in state['query_batches'])


def finish_pending(client, state, config, trajectory, usage):
    import coverage_pipeline
    pending = state.pop('_pending_continuation_batch', None)
    if not pending:
        return
    records = pending['records']
    phase = REFINE if records[0].get('gap_id') else SKELETON
    state['phase'] = phase
    visible = student_state(state['dataset'], state['topic'], phase, state['timeline_events'], {}, ['MERGE'])
    coverage_pipeline.process_passages(client, state, pending['documents'], config, trajectory, usage, phase, visible)
    memory.sync_events(state)
    saved = memory.initialize(state)
    saved['batches_since_summary'] += 1
    state['query_batches'].append({'batch_id': records[0]['batch_id'], 'query_count': len(records),
        'before_revision': pending['before_revision'], 'after_revision': saved['revision'],
        'event_change_count': saved['revision'] - pending['before_revision'],
        'summary_version': saved['version'], 'continued_partial_batch': True,
        'gain_scope': 'continuation_only_prior_partial_gain_in_parent'})
    if phase == REFINE:
        gap = next(g for g in state['gap_memory']['gaps'] if g['gap_id'] == records[0]['gap_id'])
        gap.setdefault('attempted_queries', []).extend(r['query'] for r in records)
        gap['search_batches'] = gap.get('search_batches', 0) + 1
        gap['last_review_revision'] = None
    state['_resumed_phase1_batches'] = count_phase1_batches(state)
    coverage_pipeline.checkpoint(state, trajectory, usage)
