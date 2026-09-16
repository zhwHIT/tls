"""Replay all v9.2 failures and optionally test bounded live repairs on their evidence.

Historical invalid responses are fault-injected once. Only subsequent repair/fusion
calls are live. This is a component regression, not a resumed or full T17 rollout.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import batched_search_phase as runtime
from chronos_repro import batch_memory as memory
from chronos_repro.batch_policy import instruction, validate_action, recover_duplicate_queries
from chronos_repro.compact_context import CompactContextClient
from chronos_repro.envfile import load_env_file
from chronos_repro.fact_memory import validate_facts, recover_fact_lengths
from chronos_repro.full_timeline import (DuplicateAppendError, validate_merge_operations,
                                         recover_exact_duplicate_appends)
from chronos_repro.limited_llm import LimitedDeepSeekClient
from chronos_repro.llm import ChatResult
from chronos_repro.llm_cache import CachedLLMClient
from chronos_repro.run_guard import exclusive_run, source_binding
from chronos_repro.snapshot import sha256
from chronos_repro.tisa_data import parse_json_object
from chronos_repro.tisa_rollout import SKELETON
from chronos_repro.token_budget import TokenBudgetClient
from run_tisa_two_phase_annotation import execute_merge, save_json


class SeededClient:
    def __init__(self, client, payload):
        self.client, self.model, self.seed = client, client.model, copy.deepcopy(payload)
    def chat(self, messages, temperature=0):
        if self.seed is not None:
            seed, self.seed = self.seed, None
            return ChatResult(json.dumps(seed), 'historical_fault_injection', {}, None, attempts=0)
        return self.client.chat(messages, temperature=temperature)
    def accept_last_response(self, payload):
        self.client.accept_last_response(payload)


def historical_case(folder):
    trace = json.loads((folder / 'trajectory.json').read_text(encoding='utf-8'))
    config = json.loads((folder / 'run_config.json').read_text(encoding='utf-8'))
    failed = [a for a in trace['audits'] if a['stage'] == 'FAILED_LABEL_VALIDATION']
    target = [a['response_sha256'] for f in failed for a in f['attempts'] if 'response_sha256' in a]
    candidates = []
    if not target:
        verify = next(s for s in reversed(trace['steps']) if s['action'] == 'VERIFY')
        candidates = [r for r in verify['model_output']['candidates']
                      if r['status'] in {'SUPPORTED', 'CONFLICTED'} and r['relevance_pass'] and r['contribution_pass']]
    for path in sorted((folder / 'api_cache').glob('*.json')):
        raw = json.loads(path.read_text(encoding='utf-8'))['response']['text']
        if target and hashlib.sha256(raw.encode()).hexdigest() != target[-1]:
            continue
        try:
            payload = parse_json_object(raw)
        except ValueError:
            continue
        if not target:
            operations = payload.get('operations', [])
            if not operations or {r.get('candidate_id') for r in operations} != {c['candidate_id'] for c in candidates}:
                continue
            try:
                validate_merge_operations(operations, candidates, trace['final_events'], exact_duplicate_guard=True)
            except DuplicateAppendError:
                pass
            else:
                continue
        return trace, config, payload, candidates, path
    raise ValueError('Cannot bind historical failure: ' + str(folder))


def run_case(trace, config, payload, candidates, client=None):
    state = {'dataset': trace['dataset'], 'topic': trace['topic'], 'phase': SKELETON,
             'timeline_events': copy.deepcopy(trace['final_events']),
             'controller_memory': copy.deepcopy(trace['controller_memory']),
             'exploration_memory': copy.deepcopy(trace.get('final_exploration_memory', {})),
             'search_history': []}
    audit = {'steps': [], 'audits': []}
    usage = {k: 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'logical_calls', 'http_attempts')}
    if 'facts' in payload:
        kind = 'fact_length'
        # Offline covers the full original response; live tests one overlong and
        # one valid original row with all of their supporting archived events.
        if client is not None:
            long = next(r for r in payload['facts'] if len(r['text']) > 200)
            valid = next((r for r in payload['facts'] if len(r['text']) <= 200), None)
            payload = {'action': 'MEMORY_UPDATE', 'facts': [long] + ([valid] if valid else [])}
        allowed = {i for f in payload['facts'] for i in f['event_ids']}
        current = {e['event_id'] for e in state['timeline_events']}
        recovery = lambda p: recover_fact_lengths(p, allowed, current, state['timeline_events'])
        if client is None:
            out, metadata = recovery(payload)
        else:
            evidence = [memory.event_view(e) for e in state['timeline_events'] if e['event_id'] in allowed]
            request = {'topic': state['topic'], 'verified_events': evidence,
                       'objective': 'Summarize only these supported facts. Target 140 characters per text, hard '
                       'limit 6-200 including spaces. Preserve dates, uncertainty and negation. Cite event_ids.',
                       'output': {'action': 'MEMORY_UPDATE', 'facts': [{'text': 'dated factual statement', 'event_ids': ['event ID']}]}}
            out = runtime._call(SeededClient(client, payload), state, config, audit, usage, 'FACT_MEMORY', request,
                                lambda p: validate_facts(p, allowed, current), recovery=recovery)
            metadata = audit['steps'][-1]['observation']
        if out is None:
            raise ValueError('Probe could not obtain usable factual memory')
    elif payload.get('action') == 'SEARCH':
        kind = 'duplicate_query'
        history = state['controller_memory']['query_history']
        recover = lambda p: recover_duplicate_queries(p, history, maximum_queries=3)
        if client is None:
            out, metadata = recover(payload)
        else:
            request = instruction(memory.policy_view(state, SKELETON), 3)
            out = runtime._call(SeededClient(client, payload), state, config, audit, usage, 'BATCH_POLICY', request,
                                lambda p: validate_action(p, history, maximum_queries=3), policy=True, recovery=recover)
            metadata = audit['steps'][-1]['observation']
    else:
        kind = 'duplicate_append'
        if client is None:
            out, metadata = recover_exact_duplicate_appends(payload['operations'], candidates, state['timeline_events'])
        else:
            visible = {'dataset': state['dataset'], 'topic': state['topic'], 'phase': SKELETON,
                       'events': copy.deepcopy(state['timeline_events']), 'memory': {}, 'valid_actions': ['MERGE']}
            out, applied = execute_merge(SeededClient(client, payload), state, candidates, config, audit, usage,
                                         SKELETON, visible)
            metadata = {'applied': applied, 'event_count_after': len(state['timeline_events'])}
    return {'topic': trace['topic'], 'kind': kind, 'status': 'passed', 'output': out,
            'recovery': metadata, 'trace': audit, 'usage': usage}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--allow-api', action='store_true')
    args = parser.parse_args()
    project, output = Path(args.project_root).resolve(), Path(args.output_dir).resolve()
    if not output.is_relative_to(project):
        raise ValueError('Output must remain inside project')
    if output.exists():
        raise ValueError('Preserve prior reports; use a new output directory')
    with exclusive_run(output):
        source = source_binding(project)
        result = {'source': source, 'mode': 'live_repair_after_historical_fault_injection' if args.allow_api else 'offline',
                  'full_rollout': False, 'training_ready': False, 'cases': []}
        tokens = None
        for topic in ('bpoil', 'egypt', 'finan', 'h1n1', 'iraq', 'libya', 'mj', 'syria', 'haiti'):
            folder = project / 'artifacts/tisa_v92_all_t17' / topic
            trace, config, payload, candidates, seed_path = historical_case(folder)
            if args.allow_api and tokens is None:
                load_env_file(project / '.env')
                raw = LimitedDeepSeekClient(model=config['model'], request_limit=24,
                    request_ledger_path=output / 'request_ledger.json', max_retries=0,
                    request_options={'thinking': {'type': 'disabled'}, 'max_tokens': 4096})
                settings = copy.deepcopy(config['batch_controller']['tokens'])
                settings['tokenizer_path'] = str(project / settings['tokenizer_path'])
                tokens = TokenBudgetClient(CachedLLMClient(raw, output / 'api_cache'), settings)
                client = CompactContextClient(tokens, config['compact_context'])
            try:
                row = run_case(trace, {**config, 'label_repair_attempts': 1}, payload, candidates,
                               client if args.allow_api else None)
            except Exception as error:
                row = {'topic': topic, 'status': 'failed', 'error_type': type(error).__name__, 'error': str(error)}
            row.update(original_trace_sha256=sha256(folder / 'trajectory.json'),
                       seed_response_file=str(seed_path.relative_to(project)), seed_file_sha256=sha256(seed_path))
            result['cases'].append(row)
            result['all_passed'] = len(result['cases']) == 9 and all(r['status'] == 'passed' for r in result['cases'])
            if tokens is not None:
                result.update(token_audit=tokens.statistics(), live_http_requests=raw.http_requests_total)
            save_json(output / 'result.json', result)
            print(json.dumps({k: row[k] for k in ('topic', 'status', 'error') if k in row}), flush=True)
            if args.allow_api and raw.ledger.get('balance_stop'):
                break
        return 0 if result['all_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
