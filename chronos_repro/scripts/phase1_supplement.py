"""Serial interval exploration with resumable isolated states and cumulative API accounting."""
import copy
import sqlite3
import hashlib
import json
from pathlib import Path
from chronos_repro import batch_memory as memory
from chronos_repro.supplement import choose_intervals, fresh_worker, in_scope, validate_settings
from chronos_repro.evidence_access import EvidenceReader, terms, related_events
from chronos_repro.retrieval import _bm25_path
from chronos_repro.tisa_rollout import SKELETON, student_state
import coverage_pipeline


def load_baseline(project, state, config, index):
    spec=config.get('phase1_baseline')
    if not spec or state.get('phase1_termination'):
        return
    if config.get('continuation'):
        raise ValueError('Choose baseline experiment or continuation, not both')
    path=(Path(project)/spec['path']).resolve()
    if not path.is_relative_to(Path(project).resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=spec['sha256']:
        raise ValueError('Baseline path/hash mismatch')
    saved=json.loads(path.read_text(encoding='utf-8'))
    for key in ('dataset','topic','data','index'):
        if saved[key]!=config[key]:raise ValueError('Baseline input binding mismatch: '+key)
    state['timeline_events']=copy.deepcopy(saved['events'])
    for key in ('candidate_pool','search_history','query_batches'):
        state[key]=copy.deepcopy(saved[key])
    reader=coverage_pipeline.reader_for(state,index,config)
    reader.passages={p['id']:copy.deepcopy(p) for p in saved['evidence_passages']}
    reader.processed=set(saved['processed_passage_ids'])
    # Do not mark whole documents loaded: only previously supplied passages were exported.
    state['incomplete_extraction']=[{'passage_ids':[k],'reason':'baseline passage not fully extracted'}
                                  for k in reader.passages if k not in reader.processed]
    state['phase1_termination']='audited_phase1_baseline'
    state['phase1_baseline_binding']={'path':str(path),'sha256':spec['sha256'],
                                    'prior_full_run_ledger':saved['prior_full_run_ledger'],'training_ready':False}
    memory.sync_events(state)
    memory.initialize(state)['query_history']=copy.deepcopy(saved['search_history'])


def populate_reader(reader):
    """Build an event-time candidate pool from the frozen topic, never Gold files."""
    uri = _bm25_path(reader.index).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        ids = [str(r[0]) for r in db.execute('SELECT doc_id FROM documents WHERE topic=? ORDER BY doc_id', (reader.topic,))]
    for offset in range(0, len(ids), 200):
        reader.add_results([{'id': i} for i in ids[offset:offset+200]])
    if reader.temporal_annotations is not None:
        from chronos_repro.frozen_timex import attach_annotations
        for p in reader.passages.values():
            anns = reader.temporal_annotations.get(p['document_id'], [])
            attach_annotations(p, anns, maximum=max(12, len(anns)))


def export_worker(state, trace):
    # Runtime objects and callbacks are deliberately excluded from immutable snapshots.
    saved = {k: copy.deepcopy(v) for k, v in state.items() if not k.startswith('_')}
    saved['extraction_cache'] = copy.deepcopy(state.get('_extraction_cache', {}))
    saved['processed_passage_ids'] = sorted(state['_evidence_reader'].processed)
    return {'state': saved, 'trajectory': copy.deepcopy(trace)}


def run(client, state, config, index, trajectory, usage):
    import batched_search_phase as batch
    from batch_continuation import finish_pending
    from run_tisa_two_phase_annotation import execute_merge, model_step
    validate_settings(config)
    opts = config['phase1_supplement']
    record = state.get('phase1_supplement')
    if record and record.get('status') == 'complete':
        return
    if record is None:
        if any(q.get('gap_id') for q in state.get('search_history', [])):
            raise ValueError('Supplement must start from phase-one baseline, not a phase-two final timeline')
        record = {'status': 'rereading', 'baseline_events': copy.deepcopy(state['timeline_events']),
                  'options': copy.deepcopy(opts), 'reread_done': [], 'children': {}, 'merged': [],
                  'training_ready': False, 'workers': 1}
        state['phase1_supplement'] = record
    if record['options'] != opts:
        raise ValueError('Cannot change supplement settings during continuation')
    reader = coverage_pipeline.reader_for(state, index, config)
    if record['status'] == 'rereading':
        if 'reread_queue' not in record:
            ids = {i for q in state.get('incomplete_extraction', [])
                   if 'validation' not in q.get('reason', '').lower() for i in q['passage_ids']}
            qterms = terms(' '.join(state.get('keywords', [])) or state['topic'])
            rows = [reader.passages[i] for i in ids if i in reader.passages and i not in reader.processed]
            rows.sort(key=lambda p: (-len(qterms & terms(p['text'])), p['id']))
            # Identical evidence content is one continuation task.
            unique = {}
            for p in rows:
                unique.setdefault(p.get('content_sha256', p['id']), p['id'])
            record['reread_queue'] = list(unique.values())[:opts['reread_passages']]
        for pid in record['reread_queue']:
            if pid in record['reread_done']:
                continue
            state['_reread_pages'] = opts['reread_pages']
            try:
                view = student_state(state['dataset'], state['topic'], SKELETON, state['timeline_events'], {}, ['MERGE'])
                coverage_pipeline.process_passages(client, state, [reader.passages[pid]], config, trajectory, usage, SKELETON, view)
            finally:
                state.pop('_reread_pages', None)
            record['reread_done'].append(pid)
            coverage_pipeline.checkpoint(state, trajectory, usage)
        record['status'] = 'planning'
    if record['status'] == 'planning':
        populate_reader(reader)
        record['intervals'] = choose_intervals(state['timeline_events'], reader.passages.values(), reader.processed,
            keywords=state.get('keywords', []), width_days=opts['interval_days'],
            maximum=opts['max_intervals'], min_documents=opts['min_documents'])
        record['status'] = 'exploring'
        coverage_pipeline.checkpoint(state, trajectory, usage)
    else:
        populate_reader(reader)  # Rebuild immutable evidence access after continuation.
    for interval in record['intervals']:
        key = interval['interval_id']
        saved = record['children'].get(key, {})
        if not saved.get('complete'):
            child = fresh_worker(state, interval)
            trace = {'steps': [], 'audits': [], 'interval': copy.deepcopy(interval), 'training_ready': False}
            local_reader = EvidenceReader(index, state['topic'], reader.settings)
            local_reader.temporal_annotations = reader.temporal_annotations  # Read-only annotation data.
            local_reader.passages = {k: copy.deepcopy(p) for k,p in reader.passages.items()
                                     if any(in_scope(a['date'], interval) for a in p.get('temporal_annotations', []))}
            local_reader.loaded_documents = {p['document_id'] for p in local_reader.passages.values()}
            local_reader.retrieval_ranks = dict(reader.retrieval_ranks)
            child['_evidence_reader'] = local_reader
            if saved:
                child.update(copy.deepcopy(saved['state']))
                child['_extraction_cache'] = child.pop('extraction_cache', {})
                local_reader.processed = set(child.pop('processed_passage_ids', []))
                trace = copy.deepcopy(saved['trajectory'])
            def commit(worker, worker_trace):
                record['children'][key] = export_worker(worker, worker_trace)
                coverage_pipeline.checkpoint(state, trajectory, usage)
            child['_checkpoint_hook'] = commit
            child_config = copy.deepcopy(config)
            child_config['phase1_max_rounds'] = opts['batches_per_interval']
            # The local state has its own traces; requests still reserve the parent's serial ledger.
            completed = {b['batch_id'] for b in child.get('query_batches', [])}
            pending = [q for q in child.get('search_history', []) if q['batch_id'] not in completed]
            if pending:
                ids = list(dict.fromkeys(i for q in pending for i in q.get('passage_ids', [])))
                documents = []
                for pid in ids:
                    doc = copy.deepcopy(local_reader.passages[pid])
                    effective = next(q['executed_filter'] for q in reversed(pending) if pid in q.get('passage_ids', []))
                    doc['event_scope'] = effective
                    doc['temporal_annotations'] = [a for a in doc.get('temporal_annotations', []) if in_scope(a['date'], effective)][:12]
                    documents.append(doc)
                child['_pending_continuation_batch'] = {'records': pending,
                    'documents': documents,
                    'before_revision': memory.initialize(child)['revision']}
                finish_pending(client, child, child_config, trace, usage)
            child['_resumed_phase1_batches'] = len(child.get('query_batches', []))
            try:
                if not child.get('phase1_termination'):
                    batch.run_phase1(client, child, child_config, index, trace, usage)
            finally:
                commit(child, trace)
            record['children'][key]['complete'] = True
            coverage_pipeline.checkpoint(state, trajectory, usage)
        child_result = record['children'][key]['state']
        # The parent assigns canonical IDs, so worker-local event-001 never collides.
        for e in child_result['timeline_events']:
            source_key = key + ':' + e['event_id']
            if source_key in record['merged']:
                continue
            if not in_scope(e['time'], interval):
                raise ValueError('Interval result escaped event scope')
            candidate = {'candidate_id': source_key, 'status': 'CONFLICTED' if e.get('conflict') else 'SUPPORTED',
                         'event': {k: copy.deepcopy(e.get(k)) for k in ('time','summary','actors','location')},
                         'confidence': e['confidence'], 'evidence_ids': copy.deepcopy(e['evidence_ids']),
                         'date_evidence': copy.deepcopy(e.get('date_evidence')), 'relevance_pass': True, 'contribution_pass': True}
            neighbors = related_events(state['timeline_events'], [candidate],
                                       config['coverage_pipeline'].get('merge_context_events', 8))
            view = student_state(state['dataset'], state['topic'], SKELETON, neighbors, {}, ['MERGE'])
            execute_merge(client, state, [candidate], config, trajectory, usage, 'PHASE1_SUPPLEMENT', view)
            if not any(c['candidate_id'] == source_key for c in state.setdefault('candidate_pool', [])):
                state['candidate_pool'].append({**copy.deepcopy(candidate), 'merge_processed': True,
                    'source_passage_ids': [i for i in candidate['evidence_ids'] if i in reader.passages]})
            memory.sync_events(state)
            record['merged'].append(source_key)
            coverage_pipeline.checkpoint(state, trajectory, usage)
    state['timeline_events'].sort(key=lambda e: (e['time'], e['event_id']))
    memory.sync_events(state)
    batch.refresh_summary(client, state, config, trajectory, usage, force=True)
    record['status'] = 'complete'
    model_step(trajectory, 'PHASE1_SUPPLEMENT', 'COMPLETE', {'intervals':record['intervals']},
               {'event_count':len(state['timeline_events'])}, {'training_ready':False}, 'diagnostic_supplement')
    coverage_pipeline.checkpoint(state, trajectory, usage)
