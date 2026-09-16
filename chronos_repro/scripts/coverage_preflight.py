"""Local-only preflight and cumulative request guards for coverage runs."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

from chronos_repro.evidence_access import EvidenceReader
from chronos_repro.exploration_memory import load_keywords
from chronos_repro.limited_llm import LimitedDeepSeekClient
from chronos_repro.retrieval import _bm25_path, read_index_metadata, search
from chronos_repro.snapshot import MANIFEST, sha256


def coverage_envelope(config):
    if not config.get('coverage_pipeline', {}).get('enabled'):
        raise ValueError('This preflight is for coverage-enabled runs')
    if config.get('phase2_teacher_guidance', False):
        raise ValueError('Evaluation rollout must not expose Gold to the model')
    settings = config['coverage_pipeline']
    counts = {key: config[key] for key in ('phase1_max_rounds', 'phase2_max_gap_cycles', 'max_api_requests', 'top_k')}
    counts.update({key: settings[key] for key in ('passages_per_search', 'passage_chars', 'verification_batch_passages', 'extraction_pages')})
    batch = config.get('batch_controller', {})
    if batch.get('enabled'):
        from chronos_repro.fact_memory import fact_text_limits
        fact_text_limits(batch)
        if config.get('phase2_teacher_guidance') is not False:
            raise ValueError('Batch controller requires explicit phase2_teacher_guidance=false')
        if type(batch.get('max_queries')) is not int or not 1 <= batch['max_queries'] <= 3:
            raise ValueError('max_queries must be 1..3')
        if type(batch.get('summary_interval_batches')) is not int or batch['summary_interval_batches'] < 2:
            raise ValueError('Summary interval must be at least two batches')
        tokens = batch.get('tokens', {})
        if not (0 < tokens.get('preflight_limit', 0) <= tokens.get('input_limit', 0) <= 4096):
            raise ValueError('Batch token limits must be positive, ordered, and at most 4096')
    if any(type(v) is not int or v < 1 for v in counts.values()):
        raise ValueError('Run limits must be positive integers')
    if settings['passage_chars'] > 3200 or settings['passages_per_search'] > 8:
        raise ValueError('Coverage transport exceeds the planned 8 x 3200-character envelope')
    return {
        'topic': config['topic'], 'model': config['model'], 'endpoint': 'https://api.deepseek.com',
        'max_http_requests_across_restarts': config['max_api_requests'],
        'max_search_rounds': config['phase1_max_rounds'] + config['phase2_max_gap_cycles'],
        'max_passage_selection_slots': (config['phase1_max_rounds'] + config['phase2_max_gap_cycles']) * settings['passages_per_search'] * (batch.get('max_queries', 1) if batch.get('enabled') else 1),
        'max_queries_per_batch': batch.get('max_queries', 1) if batch.get('enabled') else 1,
        'passages_per_search': settings['passages_per_search'],
        'body_chars_per_passage': settings['passage_chars'], 'preceding_context_chars_per_passage': 400,
        'other_model_inputs': ['titles', 'passage IDs and metadata', 'queries', 'memory', 'candidate facts', 'timeline and quoted evidence'],
        'retransmission_note': 'Passages/derived states may be resent during pagination, repair and later decisions; selection slots are not an HTTP payload byte cap.',
        'gold_sent_to_model': False, 'stop_on_insufficient_balance': True,
    }


def build_preflight(project, config_path, config, env_file):
    envelope = coverage_envelope(config)
    project = Path(project).resolve()
    data = (project / config['data']).resolve()
    index = (project / config['index']).resolve()
    manifest_path = data / MANIFEST
    snapshot = json.loads(manifest_path.read_text(encoding='utf-8'))
    entries = [row for row in snapshot['files'] if row['path'].startswith(config['topic'] + '/')]
    if not entries:
        raise ValueError('Topic is missing from the frozen snapshot manifest')
    for entry in entries:
        source = (data / entry['path']).resolve()
        if not source.is_relative_to(data) or not source.is_file() or sha256(source) != entry['sha256']:
            raise ValueError('Frozen topic file failed hash verification: ' + entry['path'])
    bm25 = _bm25_path(index)
    if not bm25.is_file():
        raise FileNotFoundError(bm25)
    metadata = read_index_metadata(index)
    if (metadata.get('snapshot') or {}).get('snapshot_id') != snapshot['snapshot_id']:
        raise ValueError('Index and corpus snapshot IDs disagree')
    dense = metadata.get('dense', {})
    if not dense.get('complete') or Path(dense.get('bm25_index', '')).resolve() != bm25:
        raise ValueError('Dense collection is incomplete or bound to another BM25 index')
    model = Path(dense['model']['local_path'])
    if not model.is_dir() or not (model / dense['onnx_file']).is_file():
        raise ValueError('Local embedding model or ONNX weights are missing')
    with sqlite3.connect(bm25.as_uri() + '?mode=ro', uri=True) as connection:
        count, first, last = connection.execute('SELECT COUNT(*),MIN(timestamp),MAX(timestamp) FROM documents WHERE topic=?', [config['topic']]).fetchone()
    if not count or count != metadata['topic_counts'].get(config['topic']):
        raise ValueError('Topic document counts disagree')
    keywords = load_keywords(data / config['topic'])
    query = ' '.join(keywords) + ' key events timeline'
    started = time.perf_counter()
    results = search(index, [query], config['top_k'], config['dataset'] + ' ' + config['topic'])
    reader = EvidenceReader(index, config['topic'], config['coverage_pipeline'])
    reader.add_results(results)
    passages = reader.select(query)
    if not results or not passages:
        raise ValueError('Local hybrid retrieval or passage access returned no evidence')
    if any(len(p['text']) > envelope['body_chars_per_passage'] or len(p.get('context_before', '')) > 400 for p in passages):
        raise ValueError('Retrieved preview exceeds transport envelope')
    return {
        'status': 'offline_preflight_passed_not_api_authorized', 'api_calls': 0,
        'python_executable': sys.executable, 'env_file_exists': Path(env_file).is_file(),
        'binding': {'config_sha256': sha256(Path(config_path)), 'index_manifest_sha256': sha256(index),
                    'dense_manifest_sha256': sha256(index.parent / metadata['dense_index'] / 'manifest.json'),
                    'snapshot_manifest_sha256': sha256(manifest_path), 'snapshot_id': snapshot['snapshot_id']},
        'authorization_envelope': envelope,
        'snapshot_checks': {'topic_files_hashed': len(entries), 'topic_document_count': count,
                            'publication_metadata_from': first, 'publication_metadata_to': last,
                            'publication_dates_are_not_event_boundaries': True},
        'retrieval_probe': {'query': query, 'result_count': len(results), 'selected_passage_count': len(passages),
                            'elapsed_seconds': round(time.perf_counter() - started, 3),
                            'reader': reader.summary(), 'result_ids': [r['id'] for r in results]},
        'limitations': ['Does not establish semantic coverage or event entailment',
                        'Does not re-audit every vector value or run the full controller',
                        'Reads no API key and does not test account balance'],
    }


def guarded_coverage_client(config, output, *, allow_replay_from_start=False):
    """Refuse legacy unknown counters; a new scope requires a new approved run."""
    coverage_envelope(config)
    output = Path(output)
    ledger = output / 'request_ledger.json'
    previous_path = output / 'trajectory.json'
    if previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding='utf-8'))
        if previous.get('status') in {'ok', 'stopped_insufficient_balance'}:
            raise ValueError('Completed or balance-stopped run cannot be restarted')
        if not ledger.exists():
            raise ValueError('Existing run has no cumulative request ledger; do not guess historical usage')
        if not allow_replay_from_start:
            raise ValueError('Failed run requires explicit --replay-from-start; checkpoint continuation is not implemented')
    if not ledger.exists() and any((output / 'api_cache').glob('*.json')):
        raise ValueError('Cached responses exist without a request ledger')
    from chronos_repro.runtime_profile import REQUEST_OPTIONS
    return LimitedDeepSeekClient(model=config['model'], max_retries=4, retry_backoff_seconds=5.0,
                                 request_options=REQUEST_OPTIONS,
                                 request_limit=config['max_api_requests'], request_ledger_path=ledger)
