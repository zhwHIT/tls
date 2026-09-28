"""Offline prompt-budget check; no model calls or retrieval, no Gold in policy."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from chronos_repro.batch_memory import chronology_frontier, policy_view
from chronos_repro.batch_policy import instruction
from chronos_repro.compact_context import compact_instruction
from chronos_repro.token_budget import TokenBudgetClient
from batched_search_phase import policy_system


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    source = ROOT / 'artifacts/beckham_joint_evidence_stopped_audit/checkpoint.json'
    raw = source.read_bytes()
    saved = json.loads(raw)
    state = {**copy.deepcopy(saved), 'timeline_events': copy.deepcopy(saved['final_events'])}
    state['gap_memory'] = state.get('gap_memory') or {}
    config = json.loads((source.parent / 'run_config.json').read_text(encoding='utf-8'))
    settings = {**config['batch_controller']['tokens'],
                'tokenizer_path': str(ROOT / 'artifacts/deepseek_v4_tokenizer/tokenizer.json')}
    budget = TokenBudgetClient(SimpleNamespace(model='offline-no-network'), settings)

    def estimate(payload):
        return budget.estimate([{'role': 'system', 'content': policy_system(state)},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}])

    # Size stress only: add the final frontier to each historical input. This does
    # not replay earlier decisions, and the final event is not shown to any model.
    history = []
    for step in saved['steps']:
        if step['action'] != 'SEARCH':
            continue
        payload = copy.deepcopy(step['model_input'])
        payload['state']['chronology_frontier'] = chronology_frontier(state)
        assert compact_instruction(payload)['state']['chronology_frontier'] == payload['state']['chronology_frontier']
        raw_tokens = estimate(payload)
        removed = 0
        # Mirror the existing fallback: shorten the page, never individual reasons.
        leads = payload['state']['pending_leads']
        while estimate(payload) > settings['preflight_limit'] and len(leads) > 1:
            leads.pop()
            removed += 1
            counts = payload['state']['pending_lead_counts']
            counts['shown'] = len(leads)
            counts['omitted'] += 1
        history.append({'step_id': step['step_id'], 'unpaged_tokens': raw_tokens,
                        'estimated_tokens': estimate(payload), 'removed_from_visible_page': removed})
    limit = config['batch_controller'].get('pending_lead_limit', 8)
    while True:
        view = policy_view(state, 'SKELETON_EXPLORATION', recent_queries=3, pending_lead_limit=limit)
        payload = {'protocol': 'batch-v9', **instruction(view)}
        tokens = estimate(payload)
        if tokens <= settings['preflight_limit'] or limit == 1:
            break
        limit -= 1
    assert tokens <= settings['preflight_limit']
    assert all(r['estimated_tokens'] <= settings['preflight_limit'] for r in history), history
    assert source.read_bytes() == raw
    report = {'api_calls': 0, 'kind': 'offline_prompt_budget_stress_not_policy_replay',
        'historical_inputs': history, 'max_historical_tokens': max(r['estimated_tokens'] for r in history),
        'current_policy_tokens': tokens, 'current_pending_lead_limit': limit,
        'frontier': view['chronology_frontier'], 'preflight_limit': settings['preflight_limit'],
        'source_unchanged': True, 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'new_gold_coverage': None}
    out = ROOT / 'artifacts/early_career_policy_offline'
    out.mkdir(exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
