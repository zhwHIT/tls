"""Post-run date recall and joint-evidence/lead-interface audit; no API calls."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.evaluate import evaluate_dates


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(folder):
    path = folder / 'trajectory.json'
    trace = json.loads(path.read_text(encoding='utf-8'))
    config = json.loads((folder / 'run_config.json').read_text(encoding='utf-8'))
    assert config['first_phase_only'] and not config.get('phase1_baseline')
    assert not config.get('phase1_supplement', {}).get('enabled')
    assert {s['phase'] for s in trace['steps']} <= {'SKELETON_EXPLORATION'}
    gold_path = ROOT / config['data'] / config['topic'] / 'timelines.jsonl'
    gold = {date.fromisoformat(d[:10]): tuple(s) for d, s in json.loads(gold_path.read_text(encoding='utf-8'))}
    assert len(gold) == 16
    events = trace['final_events']
    predicted = {date.fromisoformat(e['time']): (e['summary'],) for e in events}
    score = evaluate_dates(predicted, [gold])
    hits = sorted(d.isoformat() for d in gold.keys() & predicted.keys())
    verifies = [s for s in trace['steps'] if s['action'] == 'VERIFY']
    searches = [s for s in trace['steps'] if s['action'] == 'SEARCH']
    policies = [s for s in trace['steps'] if s['action'] in {'SEARCH', 'STOP'} and s.get('model_input', {}).get('stage') == 'BATCH_POLICY']
    batches = [s for s in trace['steps'] if s['action'] == 'BATCH_RETRIEVAL']
    queries = [q for s in batches for q in s['observation']['queries']]
    candidates = json.loads((folder / 'candidate_pool.json').read_text(encoding='utf-8'))
    leads = trace.get('final_exploration_memory', {}).get('lead_queue', [])
    passages = {p['id']: p for s in verifies for p in s['model_input']['tool_observation']['retrieved_documents']}
    errors = []
    for event in events:
        try:
            validate_date_evidence(event['time'], event.get('date_evidence'), list(passages.values()), event['evidence_ids'])
        except (ValueError, KeyError) as error:
            errors.append({'event_id': event['event_id'], 'error': str(error)})
    reader = json.loads((folder / 'evidence_reader_manifest.json').read_text(encoding='utf-8'))
    selection_checks = [r['tokens'] <= r['token_budget'] and r['visible_chars'] <= r['visible_char_budget']
                        for r in reader.get('selection_history', [])]
    ledger = json.loads((folder / 'request_ledger.json').read_text(encoding='utf-8'))
    code = json.loads((folder / 'code_binding.json').read_text(encoding='utf-8'))
    changed = [rel for rel, digest in code['files'].items() if sha(ROOT / rel) != digest]
    exposure = [{'step_id': s['step_id'], 'counts': s['model_input']['state'].get('pending_lead_counts'),
                 'leads': s['model_input']['state'].get('pending_leads', [])} for s in policies]
    exposed_ids = {l['lead_id'] for row in exposure for l in row['leads']}
    displayed = [l for row in exposure for l in row['leads']]
    supported_revisions = [c for c in candidates if c['status'] == 'SUPPORTED' and c.get('revises_candidate_ids')]
    old = json.loads((ROOT / 'artifacts/beckham_sentence_queue_stopped_audit/report.json').read_text(encoding='utf-8'))
    report = {
        'metric': 'gold_date_recall', 'runtime_status': trace['status'], 'error': trace.get('error'),
        'phase1_termination': trace.get('phase1_termination'), 'phase2_termination': trace.get('phase2_termination'),
        'scope': 'Fresh first phase only; adaptive queries; no supplementation or phase two.',
        'gold_date_count': 16, 'matched_gold_date_count': len(hits), 'gold_date_recall': score.recall,
        'matched_gold_dates': hits, 'missed_gold_dates': sorted(d.isoformat() for d in gold if d.isoformat() not in hits),
        'per_gold_date': [{'date': d.isoformat(), 'gold_summaries': list(summary), 'hit': d.isoformat() in hits,
                          'predicted_events': [e for e in events if e['time'] == d.isoformat()]}
                         for d, summary in sorted(gold.items())],
        'final_event_count': len(events), 'predicted_unique_date_count': len(predicted),
        'previous_stopped_run': {'matched': old['matched_gold_count'], 'total': 16,
            'recall': old['gold_date_recall'], 'matched_dates': old['matched_gold_dates'],
            'completed_first_phase': False, 'counts': old['counts']},
        'recall_change_percentage_points': 100 * (score.recall - old['gold_date_recall']),
        'newly_matched_dates': sorted(set(hits) - set(old['matched_gold_dates'])),
        'previously_matched_now_missing': sorted(set(old['matched_gold_dates']) - set(hits)),
        'counts': {'completed_batches': len(trace.get('query_batches', [])), 'recorded_batches': len(batches),
            'queries': len(queries), 'retrieved_documents': len({str(i) for q in queries for i in q['result_ids']}),
            'verify_calls': len(verifies), 'joint_verify_calls': sum(len(s['model_input']['tool_observation']['retrieved_documents']) > 1 for s in verifies),
            'verify_input_passage_counts': dict(Counter(len(s['model_input']['tool_observation']['retrieved_documents']) for s in verifies)),
            'candidate_status': dict(Counter(c['status'] for c in candidates)),
            'queries_bound_to_leads': sum(bool(q.get('target_lead_ids')) for q in queries),
            'final_leads': len(leads), 'lead_status': dict(Counter(l['status'] for l in leads)),
            'distinct_exposed_leads': len(exposed_ids), 'leads_with_attempts': sum(bool(l.get('attempted_queries')) for l in leads),
            'supported_reassessments': len(supported_revisions),
            'candidates_with_committed_resolution': sum(bool(c.get('resolution_event_ids')) for c in candidates),
            'missing_fields_in_candidates': sum('missing_fields' in c for c in candidates)},
        'supported_reassessments': supported_revisions,
        'retirement_leads': [l for l in leads if 'retir' in l['summary'].lower()],
        'query_history': queries, 'policy_lead_exposure': exposure,
        'date_validation_errors': errors,
        'checks': {'first_phase_only': True, 'all_16_dates_in_denominator': True,
            'selection_budgets_pass': all(selection_checks), 'selection_count': len(selection_checks),
            'all_displayed_leads_have_reason': all(bool(l.get('reason')) for l in displayed),
            'all_events_have_date_evidence': all(bool(e.get('date_evidence', {}).get('time_expression')) for e in events),
            'runtime_code_unchanged': not changed, 'changed_runtime_files': changed},
        'usage': trace['usage'], 'request_accounting': ledger,
        'source_hashes': {'trajectory': sha(path), 'gold': sha(gold_path), 'code_binding': sha(folder / 'code_binding.json')},
        'limitations': ['Exact date matching does not establish semantic event correctness.',
            'The previous run was stopped partway through batch 12; this is not a matched-budget causal comparison.',
            'Queries are adaptive; improvements cannot be attributed to a single modification.',
            'A syntactically valid date evidence object can still contain event-time interpretation errors.']}
    (folder / 'gold_date_coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# David Beckham 新方案第一阶段评测', '',
        f"状态：{report['runtime_status']}；第一阶段终止原因：{report['phase1_termination']}。",
        f"Gold 日期覆盖率：**{len(hits)}/16 = {score.recall:.2%}**。",
        f"旧中止快照：1/16；新命中日期：{', '.join(report['newly_matched_dates']) or '无'}。", '',
        '|Gold 日期|日期命中|输出事件|', '|---|---|---|']
    for row in report['per_gold_date']:
        text = '; '.join(e['summary'].replace('|', '/') for e in row['predicted_events'])
        lines.append(f"|{row['date']}|{'是' if row['hit'] else '否'}|{text}|")
    lines += ['', '日期命中不自动证明事件语义一致。旧结果为中止快照，两次查询序列和实际调用量不同，不能视为等预算消融。']
    (folder / 'gold_date_coverage.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, default=ROOT / 'artifacts/beckham_joint_evidence_phase1_api')
    args = parser.parse_args()
    report = evaluate(args.run_dir.resolve())
    print(json.dumps({k: report[k] for k in ('runtime_status', 'error', 'phase1_termination', 'matched_gold_date_count',
        'gold_date_recall', 'matched_gold_dates', 'final_event_count', 'counts', 'checks', 'usage', 'request_accounting')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
