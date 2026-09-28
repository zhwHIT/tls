"""Compare current flow with topic-only baseline; no API calls."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    baseline = json.loads((ROOT / 'baseline.json').read_text(encoding='utf-8'))
    rows = []
    for previous in baseline:
        topic = previous['topic']
        score_path = ROOT / ('crisis_' + topic) / 'gold_date_coverage.json'
        if not score_path.exists():
            continue
        before = previous['evaluation']
        after = json.loads(score_path.read_text(encoding='utf-8'))
        assert before['gold_sha256'] == after['gold_sha256']
        old_hits, new_hits = set(before['matched_gold_dates']), set(after['matched_gold_dates'])
        rows.append({
            'topic': topic, 'runtime_status': after['runtime_status'],
            'gold_dates': after['gold_date_count'],
            'before_hits': before['matched_gold_date_count'],
            'after_hits': after['matched_gold_date_count'],
            'before_recall': before['gold_date_recall'],
            'after_recall': after['gold_date_recall'],
            'delta_percentage_points': 100 * (after['gold_date_recall'] - before['gold_date_recall']),
            'newly_matched_dates': sorted(new_hits - old_hits),
            'no_longer_matched_dates': sorted(old_hits - new_hits),
            'before_batches': before['completed_batches'], 'after_batches': after['completed_batches'],
            'before_queries': before['query_count'], 'after_queries': after['query_count'],
            'before_termination': before['phase1_termination'], 'after_termination': after['phase1_termination'],
            'before_retrieved_date_hits': before['retrieved_extracted_date_hits'],
            'after_retrieved_date_hits': after['retrieved_extracted_date_hits'],
            'after_retrieved_but_not_selected': after['retrieved_but_not_selected'],
        })
    valid = [r for r in rows if r['runtime_status'] == 'ok']
    totals = {}
    if valid:
        totals = {
            'topics': len(valid),
            'before_macro_recall': sum(r['before_recall'] for r in valid) / len(valid),
            'after_macro_recall': sum(r['after_recall'] for r in valid) / len(valid),
            'before_hits': sum(r['before_hits'] for r in valid),
            'after_hits': sum(r['after_hits'] for r in valid),
            'gold_dates': sum(r['gold_dates'] for r in valid),
        }
        totals['before_micro_recall'] = totals['before_hits'] / totals['gold_dates']
        totals['after_micro_recall'] = totals['after_hits'] / totals['gold_dates']
    report = {'complete': len(valid) == len(baseline), 'metric': 'Exact Gold date recall',
              'rows': rows, 'totals': totals,
              'limitations': ['One run per topic; maximum budgets fixed, realized queries and costs can differ.',
                             'Baseline Syria resumed with SEARCH schema recovery; other baseline Crisis runs preceded that fix.']}
    (ROOT / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# Crisis current flow with original keywords', '',
             'Original topic-only keywords retained. Current developments prompt; no completion leads or yield feedback. First phase only.', '',
             '| Topic | Before | After | Delta (pp) | Runtime | Stop |',
             '|---|---:|---:|---:|---|---|']
    for r in rows:
        lines.append(f"| {r['topic']} | {r['before_hits']}/{r['gold_dates']} ({r['before_recall']:.2%}) | "
                     f"{r['after_hits']}/{r['gold_dates']} ({r['after_recall']:.2%}) | "
                     f"{r['delta_percentage_points']:+.2f} | {r['runtime_status']} | {r['after_termination']} |")
    if totals:
        lines += ['', f"Macro recall: {totals['before_macro_recall']:.2%} -> {totals['after_macro_recall']:.2%}.",
                  f"Micro recall: {totals['before_micro_recall']:.2%} -> {totals['after_micro_recall']:.2%}."]
    lines += ['', 'Complete: ' + str(report['complete']), '', *report['limitations']]
    (ROOT / 'comparison.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
