"""Explicitly bounded API smoke over an already reviewed passage preview."""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import asdict
from pathlib import Path

import coverage_pipeline
from chronos_repro.data import iter_topics, load_prediction
from chronos_repro.envfile import load_env_file
from chronos_repro.evaluate import evaluate_dates
from chronos_repro.evidence_access import EvidenceReader
from chronos_repro.exploration_memory import load_keywords
from chronos_repro.full_timeline import prediction_from_timeline, evaluate_gold_coverage
from chronos_repro.llm import InsufficientBalanceError, LLMError
from chronos_repro.llm_cache import CachedLLMClient
from chronos_repro.limited_llm import LimitedDeepSeekClient, RequestLimitError
from chronos_repro.snapshot import sha256
from run_full_timeline_api_agent import save_json


def restore_saved_progress(output, previous, state, reader, trace, usage):
    """Resume committed batches only; request identity was checked by main."""
    if not previous:
        return
    manifest = json.loads((output / 'evidence_reader_manifest.json').read_text(encoding='utf-8'))
    processed = set(manifest['processed_passage_ids'])
    if not processed.issubset(reader.passages):
        raise ValueError('Saved progress contains passages outside the approved preview')
    state['timeline_events'] = copy.deepcopy(previous['final_events'])
    state['candidate_pool'] = json.loads((output / 'candidate_pool.json').read_text(encoding='utf-8'))
    reader.mark_processed([reader.passages[key] for key in processed])
    trace['steps'] = copy.deepcopy(previous.get('steps', []))
    trace['audits'] = copy.deepcopy(previous.get('audits', []))
    trace['resumed_processed_passage_count'] = len(processed)
    usage.update(previous.get('usage', {}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--preview', required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--allow-api', action='store_true')
    args = parser.parse_args()
    if not args.allow_api:
        raise SystemExit('API is disabled; explicit --allow-api is required after user approval')
    config_path, preview_path = Path(args.config), Path(args.preview)
    config = json.loads(config_path.read_text(encoding='utf-8'))
    preview = json.loads(preview_path.read_text(encoding='utf-8'))
    docs = preview['passages']
    if not 1 <= len(docs) <= 8 or any(len(d['text']) > 3200 or len(d.get('context_before', '')) > 400 for d in docs):
        raise ValueError('Smoke authorization envelope is at most 8 passages, 3200 body + 400 context characters each')
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    identity = {'config_sha256': sha256(config_path), 'preview_sha256': sha256(preview_path),
                'max_http_requests_per_invocation': 30, 'purpose': 'bounded reader/extraction/merge smoke, not a full rollout'}
    identity_path = output / 'run_identity.json'
    if identity_path.exists() and json.loads(identity_path.read_text(encoding='utf-8')) != identity:
        raise ValueError('Output directory belongs to another smoke request')
    save_json(identity_path, identity)
    load_env_file(args.env_file)
    previous = {}
    previous_path = output / 'trajectory.json'
    if previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding='utf-8'))
        if previous.get('status') == 'stopped_insufficient_balance':
            raise SystemExit('Previous insufficient balance stop; refusing API restart')
        if previous.get('status') == 'ok':
            raise SystemExit('Smoke already completed; use local artifacts instead of restarting')
        archive = output / 'attempts' / f"before_resume_{previous.get('http_requests_total', previous.get('http_requests_started', 0)):03d}"
        archive.mkdir(parents=True, exist_ok=True)
        for old in output.glob('*.json'):
            target = archive / old.name
            if not target.exists():
                target.write_bytes(old.read_bytes())
    prior_requests = previous.get('http_requests_total', previous.get('http_requests_started', 0))
    raw_client = LimitedDeepSeekClient(model=config['model'], request_limit=30, max_retries=4,
                                     retry_backoff_seconds=5, request_ledger_path=output / 'request_ledger.json',
                                     initial_requests=prior_requests)
    client = CachedLLMClient(raw_client, output / 'api_cache')
    settings = config['coverage_pipeline']
    reader = EvidenceReader(config['index'], config['topic'], settings)
    reader.passages = {d['id']: copy.deepcopy(d) for d in docs}
    reader.loaded_documents = {d['document_id'] for d in docs}
    keywords = load_keywords(Path(config['data']) / config['topic'])
    state = {'dataset': config['dataset'], 'topic': config['topic'], 'keywords': keywords,
             'timeline_events': [], '_evidence_reader': reader, '_output_dir': str(output)}
    trace = {'trajectory_id': 'coverage-v6-smoke-' + identity['preview_sha256'][:12],
             'dataset': config['dataset'], 'topic': config['topic'], 'steps': [], 'audits': [],
             'scope': identity['purpose'], 'approved_passage_ids': [d['id'] for d in docs]}
    usage = dict.fromkeys(['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts'], 0)
    restore_saved_progress(output, previous, state, reader, trace, usage)
    visible = {'dataset': config['dataset'], 'topic': config['topic'], 'events': [], 'memory': {}, 'valid_actions': ['MERGE']}
    status = 'running'
    try:
        remaining_docs = [d for d in docs if d['id'] not in reader.processed]
        coverage_pipeline.process_passages(client, state, remaining_docs, config, trace, usage, 'SKELETON_EXPLORATION', visible)
        coverage_pipeline.finalize(client, state, config, trace, usage)
        status = 'ok'
    except InsufficientBalanceError as error:
        status, trace['error'] = 'stopped_insufficient_balance', str(error)
    except RequestLimitError as error:
        status, trace['error'] = 'stopped_request_limit', str(error)
    except (LLMError, ValueError, KeyError, TypeError) as error:
        status, trace['error'] = 'stopped_error', str(error)
    trace.update(status=status, usage=usage, api_cache=client.statistics(), final_events=state['timeline_events'],
                 evidence_progress=reader.summary(), http_requests_started=raw_client.http_requests_started,
                 http_requests_total=raw_client.http_requests_total)
    prediction = prediction_from_timeline(state['timeline_events'])
    save_json(output / 'trajectory.json', trace)
    save_json(output / 'prediction.json', prediction)
    save_json(output / 'candidate_pool.json', state.get('candidate_pool', []))
    save_json(output / 'event_pool.json', state.get('event_pool', state['timeline_events']))
    save_json(output / 'evidence_reader_manifest.json', reader.manifest())
    # References are read only AFTER the API workflow ends; never sent to a model.
    topic = next(t for t in iter_topics(config['data']) if t.topic_id == config['topic'])
    evaluation = {'scope': identity['purpose'], 'date_score': asdict(evaluate_dates(load_prediction(output / 'prediction.json'), topic.timelines)),
                  'coverage': evaluate_gold_coverage(config['topic'], prediction, topic.timelines, config['semantic_coverage_threshold']),
                  'semantic_coverage_requires_independent_review': True}
    save_json(output / 'evaluation.json', evaluation)
    print(json.dumps({'status': status, 'events': len(state['timeline_events']), 'requests': raw_client.http_requests_started,
                      'requests_total': raw_client.http_requests_total,
                      'cache': client.statistics(), 'evidence_progress': reader.summary()}, ensure_ascii=False))
    return 0 if status == 'ok' else 3 if status == 'stopped_insufficient_balance' else 2


if __name__ == '__main__':
    raise SystemExit(main())
