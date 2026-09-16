"""Three-call prompt probe on an existing MJ run, sharing its original HTTP ledger.

This is a new source-bound component experiment, never a continuation or overwrite
of the original failed rollout. It performs no document retrieval or event merging.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import batched_search_phase as runtime
from chronos_repro.batch_policy import instruction, validate_action
from chronos_repro.compact_context import CompactContextClient
from chronos_repro.envfile import load_env_file
from chronos_repro.limited_llm import LimitedDeepSeekClient
from chronos_repro.llm_cache import CachedLLMClient
from chronos_repro.run_guard import exclusive_run, source_binding
from chronos_repro.snapshot import sha256
from chronos_repro.tisa_rollout import SKELETON, REFINE, next_open_gap
from chronos_repro.token_budget import TokenBudgetClient
from run_tisa_two_phase_annotation import save_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--allow-api', action='store_true')
    args = parser.parse_args()
    if not args.allow_api:
        parser.error('An explicitly authorized data-export scope is required')
    project, root = Path(args.project_root).resolve(), Path(args.run_dir).resolve()
    if not root.is_relative_to(project):
        raise ValueError('Run directory must remain in the project')
    with exclusive_run(root):
        config = json.loads((root / 'run_config.json').read_text(encoding='utf-8'))
        ledger = json.loads((root / 'request_ledger.json').read_text(encoding='utf-8'))
        original = json.loads((root / 'trajectory.json').read_text(encoding='utf-8'))
        if config['topic'] != 'mj' or config['max_api_requests'] != 24 or ledger['request_limit'] != 24:
            raise ValueError('Probe only supports the existing authorized MJ 24-request scope')
        if ledger['balance_stop'] or ledger['requests_started'] >= 24:
            raise ValueError('No remaining authorized API capacity')
        folder = root / 'prompt_repair_probe'
        if folder.exists():
            raise ValueError('Probe already exists; do not overwrite or silently rerun it')
        folder.mkdir()
        binding = {'source': source_binding(project), 'original_source_sha256': original['source_sha256'],
                   'original_trajectory_sha256': sha256(root / 'trajectory.json'),
                   'original_config_sha256': sha256(root / 'run_config.json'),
                   'shared_request_ledger': str(root / 'request_ledger.json'),
                   'requests_before': ledger['requests_started'], 'maximum_probe_calls': min(3, 24-ledger['requests_started']),
                   'scope': 'initial policy, gap discovery, gap policy; no query execution or full rollout'}
        save_json(folder / 'binding.json', binding)
        load_env_file(args.env_file)
        raw = LimitedDeepSeekClient(model=config['model'], request_limit=24,
                request_ledger_path=root / 'request_ledger.json', max_retries=0,
                request_options={'thinking': {'type': 'disabled'}, 'max_tokens': 4096})
        settings = copy.deepcopy(config['batch_controller']['tokens'])
        settings['tokenizer_path'] = str(project / settings['tokenizer_path'])
        tokens = TokenBudgetClient(CachedLLMClient(raw, folder / 'api_cache'), settings)
        client = CompactContextClient(tokens, config['compact_context'])
        config = {**config, 'label_repair_attempts': 0}
        state = {'dataset': config['dataset'], 'topic': config['topic'], 'phase': SKELETON,
                 'timeline_events': copy.deepcopy(original['final_events']),
                 'controller_memory': copy.deepcopy(original['controller_memory']),
                 'exploration_memory': copy.deepcopy(original['final_exploration_memory']),
                 'keywords': original['steps'][0]['model_input']['state'].get('keywords', []),
                 'search_history': []}
        trace = {'steps': [], 'audits': []}
        usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
                 'logical_calls': 0, 'http_attempts': 0}
        results = []
        status = 'running'
        try:
            initial = copy.deepcopy(original['steps'][0]['model_input']['state'])
            out = runtime._call(client, state, config, trace, usage, 'BATCH_POLICY', instruction(initial, 2),
                                lambda p: validate_action(p, [], maximum_queries=2), policy=True)
            results.append({'case': 'initial_policy', 'output': out})
            state['phase'] = REFINE
            gaps = runtime.discover_gaps(client, state, config, trace, usage)
            results.append({'case': 'gap_discovery', 'gap_count': len(gaps['gaps'])})
            active = next_open_gap(gaps)
            if active:
                out, _ = runtime.decide(client, state, config, trace, usage, REFINE, active)
                results.append({'case': 'gap_policy', 'output': out})
            status = 'passed_component_probe_not_full_rollout'
        except Exception as error:
            status = 'stopped_error'
            results.append({'error_type': type(error).__name__, 'error': str(error)})
        finally:
            save_json(folder / 'result.json', {'status': status, 'results': results,
                'requests_before': ledger['requests_started'], 'requests_after': raw.http_requests_total,
                'requests_this_probe': raw.http_requests_started, 'usage': usage,
                'trace': trace, 'token_audit': tokens.statistics(),
                'original_rollout_unchanged': sha256(root / 'trajectory.json') == binding['original_trajectory_sha256']})
        print(json.dumps({'status': status, 'results': results, 'requests_total': raw.http_requests_total}, ensure_ascii=False))
        return 0 if status.startswith('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
