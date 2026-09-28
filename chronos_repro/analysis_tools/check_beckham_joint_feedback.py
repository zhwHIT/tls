"""Offline interface/budget audit; never reruns extraction or contacts an API."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

import coverage_pipeline
from chronos_repro import batch_memory
from chronos_repro.compact_context import compact_instruction, serialize
from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.joint_verification import expand_batch, pending_candidates
from chronos_repro.lead_feedback import record_presentation
from chronos_repro.token_budget import TokenBudgetClient
from run_full_timeline_api_agent import build_verify_instruction, SYSTEM
from batched_search_phase import policy_system
from chronos_repro.batch_policy import instruction


def main():
    source = ROOT / 'artifacts/beckham_sentence_queue_stopped_audit/stopped_checkpoint.json'
    raw = source.read_bytes()
    frozen = json.loads(raw)
    config = json.loads((ROOT / 'configs/beckham_sentence_queue_phase1_api.json').read_text(encoding='utf-8'))
    config.update(coverage_extraction=True, coarse_extraction=True, max_candidates_per_round=6)
    settings = dict(config['batch_controller']['tokens'])
    settings['tokenizer_path'] = str(ROOT / 'artifacts/deepseek_v4_tokenizer/tokenizer.json')
    budget = TokenBudgetClient(SimpleNamespace(model='offline-no-network'), settings)
    def estimate(system, payload):
        return budget.estimate([{'role': 'system', 'content': system}, {'role': 'user', 'content': serialize(payload)}])
    passages = {p['id']: p for t in frozen['steps'] if t['action'] == 'VERIFY'
                for p in t['model_input']['tool_observation']['retrieved_documents']}
    s = {**copy.deepcopy(frozen), 'timeline_events': copy.deepcopy(frozen['final_events']),
         '_evidence_reader': SimpleNamespace(passages=passages)}
    s['gap_memory'] = s.get('gap_memory') or {}
    coverage_pipeline.refresh_evidence_memory(s)
    original_ids = {r['lead_id'] for r in s['exploration_memory']['lead_queue'] if r['status'] == 'OPEN'}
    exposed, pages = set(), []
    while exposed != original_ids:
        limit = 8
        while True:
            view = batch_memory.policy_view(s, 'SKELETON_EXPLORATION', pending_lead_limit=limit)
            payload = instruction(view)
            tokens = estimate(policy_system(s), payload)
            if tokens <= settings['preflight_limit'] or limit == 1:
                break
            limit -= 1
        assert tokens <= settings['preflight_limit'], 'Even a one-lead SEARCH page exceeds the input budget'
        rows = view['pending_leads']
        assert all(r.get('reason') for r in rows)
        exposed.update(r['lead_id'] for r in rows)
        pages.append({'page': len(pages) + 1, 'tokens': tokens, 'lead_ids': [r['lead_id'] for r in rows]})
        record_presentation(s, rows)
        assert len(pages) <= len(original_ids) + 1
    dates, errors = Counter(), []
    for event in frozen['final_events']:
        try:
            validate_date_evidence(event['time'], event.get('date_evidence'), list(passages.values()), event['evidence_ids'])
        except (ValueError, KeyError) as error:
            errors.append({'event_id': event['event_id'], 'error': str(error)})
        dates[(event.get('date_evidence') or {}).get('annotation_rule', 'literal')] += 1
    requests = []
    # Stress only: the saved archive is jointly available for prompt size measurement.
    # This is not a chronological replay, and produces no revised VERIFY decisions or Gold metric.
    for step in frozen['steps']:
        if step['action'] != 'VERIFY':
            continue
        seeds = step['model_input']['tool_observation']['retrieved_documents']
        batch = expand_batch(seeds, list(passages.values()), s, config['coverage_pipeline'])
        while True:
            visible = {'dataset': s['dataset'], 'topic': s['topic'], 'events': [],
                'phase': 'SKELETON_EXPLORATION', 'keywords': ['beckham', 'david beckham'],
                'memory': {'already_extracted': [], 'pending_candidates': pending_candidates(s, batch)}}
            payload = compact_instruction(build_verify_instruction({'model_visible_state': visible}, batch, config),
                                          config['compact_context'])
            tokens = estimate(SYSTEM, payload)
            if tokens <= settings['preflight_limit'] or len(batch) <= len(seeds):
                break
            batch.pop()
        requests.append({'step_id': step['step_id'], 'tokens': tokens, 'passage_count': len(batch)})
    report = {'api_calls': 0, 'kind': 'offline_interface_and_prompt_budget_audit_not_extraction_replay',
        'source_sha256': hashlib.sha256(raw).hexdigest(), 'source_unchanged': raw == source.read_bytes(),
        'candidate_counts': dict(Counter(c['status'] for c in frozen['candidate_pool'])),
        'candidate_missing_fields_count': sum('missing_fields' in c for c in frozen['candidate_pool']),
        'original_open_leads': len(original_ids), 'exposed_leads': len(exposed), 'search_pages': pages,
        'retirement_exposure_pages': [p['page'] for p in pages if 'e5f015fb9e7a7326' in p['lead_ids']],
        'timeline_events': len(frozen['final_events']),
        'events_with_date_evidence_and_time_expression': sum(bool(e.get('date_evidence', {}).get('time_expression')) for e in frozen['final_events']),
        'date_evidence_rules': dict(dates), 'date_structure_validation_errors': errors,
        'semantic_review_flags': [{'event_id': e['event_id'], 'reason': 'Saturday week is longer than the cited Saturday expression; review normalization and event-time association.'}
                                 for e in frozen['final_events'] if 'Saturday week' in e.get('date_evidence', {}).get('quote', '')],
        'verify_budget_requests': requests, 'maximum_verify_tokens': max(r['tokens'] for r in requests),
        'verify_over_budget': [r for r in requests if r['tokens'] > settings['preflight_limit']],
        'joint_request_count': sum(r['passage_count'] > 1 for r in requests),
        'new_gold_date_recall': None,
        'limitations': ['No new model outputs or event dates were generated.',
                       'Date structure checks do not establish semantic correctness.',
                       'Rotation simulation assumes continued SEARCH decisions; it does not guarantee a live controller will continue.',
                       'Passage association is a retrieval heuristic, not proof that events are identical.']}
    out = ROOT / 'artifacts/beckham_joint_feedback_offline'
    out.mkdir(exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('api_calls', 'source_unchanged', 'original_open_leads', 'exposed_leads',
        'retirement_exposure_pages', 'timeline_events', 'events_with_date_evidence_and_time_expression',
        'date_structure_validation_errors', 'maximum_verify_tokens', 'verify_over_budget', 'joint_request_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
