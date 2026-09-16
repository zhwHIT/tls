"""Local-only Gold diagnostics; never invokes an API or provides labels to policy."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from chronos_repro.data import iter_topics, load_prediction
from chronos_repro.date_evidence import explicit_dates
from chronos_repro.evaluate import evaluate_tilse
from chronos_repro.evidence_access import _bm25_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--data', required=True)
    parser.add_argument('--index', required=True)
    parser.add_argument('--topic', required=True)
    parser.add_argument('--rouge', action='store_true')
    args = parser.parse_args()
    folder = Path(args.run_dir)
    topic = next(t for t in iter_topics(Path(args.data)) if t.topic_id == args.topic)
    dates = {d.isoformat() for ref in topic.timelines for d in ref}
    path = folder / 'trajectory.json'
    if not path.exists():
        path = folder / 'checkpoint.json'
    trace = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'steps': [], 'final_events': []}
    retrieved, passages = set(), {}
    retrieval_recorded = False
    for step in trace['steps']:
        if step['action'] == 'SEARCH':
            retrieval_recorded = True
            obs = step.get('observation', {})
            retrieved.update(str(r['id']) for r in obs.get('results', []))
            for row in obs.get('documents', []):
                passages[row['id']] = row
        elif step['action'] == 'VERIFY':
            for row in step.get('model_input', {}).get('tool_observation', {}).get('retrieved_documents', []):
                passages[row['id']] = row
    if not retrieval_recorded:
        retrieved.update(str(r['document_id']) for r in passages.values() if r.get('document_id'))
    layers = {name: set() for name in ('corpus_explicit_date', 'retrieved_parent_explicit_date',
                                      'selected_passage_explicit_date', 'final_timeline_date')}
    examples = {d: [] for d in dates}
    uri = _bm25_path(Path(args.index)).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as connection:
        for doc_id, title, text in connection.execute('SELECT doc_id,title,text FROM documents WHERE topic=?', [args.topic]):
            found = explicit_dates(str(title or '') + ' ' + str(text or '')) & dates
            layers['corpus_explicit_date'].update(found)
            if str(doc_id) in retrieved:
                layers['retrieved_parent_explicit_date'].update(found)
            for day in found:
                if len(examples[day]) < 3:
                    examples[day].append(str(doc_id))
    for row in passages.values():
        source = str(row.get('title', '')) + ' ' + str(row.get('context_before', '')) + str(row.get('text', ''))
        layers['selected_passage_explicit_date'].update(explicit_dates(source) & dates)
    layers['final_timeline_date'] = {e['time'] for e in trace.get('final_events', [])} & dates
    report = {'topic': args.topic, 'run_status': trace.get('status', 'not_started'), 'api_calls': 0,
        'gold_date_count': len(dates), 'retrieval_stage_recorded': retrieval_recorded,
        'parent_scope': 'recorded_SEARCH_results' if retrieval_recorded else 'selected_passage_parents_only_no_retrieval_recall_claim',
        'retrieved_parent_count': len(retrieved), 'selected_passage_count': len(passages),
        'layers': {k: {'hits': len(v), 'date_presence_recall': len(v) / len(dates), 'dates': sorted(v)} for k, v in layers.items()},
        'per_date': [{'date': day, 'example_parent_ids': examples[day], **{k: day in v for k, v in layers.items()}} for day in sorted(dates)],
        'limitations': ['Date-string presence is not event support, semantic recall, or a retrieval oracle upper bound.',
            'Only literal full dates are counted; contextual years, relative dates and paraphrases may be missed.',
            'This private local diagnostic must not be fed back into the held-out policy run.']}
    if args.rouge and (folder / 'prediction.json').exists():
        try:
            report['timeline_rouge'] = evaluate_tilse(load_prediction(folder / 'prediction.json'), topic.timelines, 'reimpl')
        except (ImportError, RuntimeError, OSError) as error:
            report['timeline_rouge_error'] = str(error)
    (folder / 'gold_evidence_layers.private.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in {'per_date', 'layers'}} | {
        'layers': {k: {'hits': len(v), 'date_presence_recall': len(v) / len(dates)} for k, v in layers.items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
