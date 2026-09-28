"""Post-run, all-Gold exact-date coverage. Never used by the running policy."""
from pathlib import Path
from datetime import date
from collections import Counter
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chronos_repro.evaluate import evaluate_dates


def main():
    folder = ROOT / 'artifacts/beckham_sentence_queue_phase1_api'
    trajectory_path = folder / 'trajectory.json'
    trajectory = json.loads(trajectory_path.read_text(encoding='utf-8'))
    config = json.loads((folder / 'run_config.json').read_text(encoding='utf-8'))
    assert config['first_phase_only'] and not config.get('phase1_baseline')
    assert not config.get('phase1_supplement', {}).get('enabled')
    phases = sorted({s['phase'] for s in trajectory['steps']})
    assert set(phases) <= {'SKELETON_EXPLORATION'}, phases
    assert trajectory.get('phase2_termination') == 'not_requested_first_phase_only' or trajectory['status'] != 'ok'
    gold_path = ROOT / 'snapshots/entities/25ac73e52bc93b3b/David_Beckham/timelines.jsonl'
    gold = json.loads(gold_path.read_text(encoding='utf-8'))
    references = {date.fromisoformat(d[:10]): tuple(s) for d, s in gold}
    assert len(references) == 16
    events = trajectory['final_events']
    predicted = {date.fromisoformat(e['time']): (e['summary'],) for e in events}
    score = evaluate_dates(predicted, [references])
    hit_dates = sorted(d.isoformat() for d in predicted.keys() & references.keys())
    per_date = [{'date': d.isoformat(), 'gold_summaries': list(s), 'hit': d.isoformat() in hit_dates,
                 'predicted_events': [e for e in events if e['time'] == d.isoformat()]}
                for d, s in sorted(references.items())]
    reader_path = folder / 'evidence_reader_manifest.json'
    reader = json.loads(reader_path.read_text(encoding='utf-8')) if reader_path.exists() else {}
    selections = reader.get('selection_history', [])
    assert all(r['tokens'] <= r['token_budget'] and r['visible_chars'] <= r['visible_char_budget'] for r in selections)
    ledger = json.loads((folder / 'request_ledger.json').read_text(encoding='utf-8'))
    report = {'metric': 'gold_date_recall', 'scope': 'Fresh first phase only; adaptive queries; no supplement or phase two.',
              'runtime_status': trajectory['status'], 'phase1_termination': trajectory.get('phase1_termination'),
              'phases': phases, 'gold_date_count': len(references), 'matched_gold_date_count': len(hit_dates),
              'gold_date_recall': score.recall, 'matched_gold_dates': hit_dates, 'per_gold_date': per_date,
              'final_event_count': len(events), 'predicted_unique_date_count': len(predicted),
              'historical_original_first_phase': {'matched': 0, 'total': 16, 'recall': 0.0},
              'date_match_is_not_semantic_event_verification': True,
              'comparison_note': 'Adaptive fresh run with current downstream code; not a frozen-query causal ablation against the historical run.',
              'counts': {'query_batches': len(trajectory.get('query_batches', [])), 'queries': len(selections),
                         'actions': dict(Counter(s['action'] for s in trajectory['steps'])),
                         'selected_source_documents': reader.get('summary', {}).get('selected_source_document_count'),
                         'unread_candidates': len(reader.get('unread_candidates', []))},
              'usage': trajectory['usage'], 'request_accounting': ledger,
              'checks': {'first_phase_only': True, 'all_16_gold_dates_in_denominator': True,
                         'all_query_evidence_budgets_pass': True},
              'source_hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [trajectory_path, gold_path]}}
    (folder / 'gold_date_coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# David Beckham 第一阶段 Gold 日期覆盖率', '',
             f"运行状态：{report['runtime_status']}；停止原因：{report['phase1_termination']}。",
             f"Gold 日期覆盖率：**{len(hit_dates)}/16 = {score.recall:.2%}**。", '',
             '|Gold 日期|命中|输出事件|', '|---|---|---|']
    for row in per_date:
        descriptions = '; '.join(e['summary'].replace('|', '/') for e in row['predicted_events'])
        lines.append(f"|{row['date']}|{'是' if row['hit'] else '否'}|{descriptions}|")
    lines += ['', '日期精确匹配不自动证明事件语义对应。新运行允许 query 随新证据变化，不是固定历史 query 的选句消融。']
    (folder / 'gold_date_coverage.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['runtime_status','phase1_termination','matched_gold_date_count','gold_date_count','gold_date_recall','matched_gold_dates','final_event_count','counts','usage']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
