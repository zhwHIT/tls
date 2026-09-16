"""Gold-guided LOCAL retrieval probe, not autonomous evaluation or training data."""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import re

from chronos_repro.data import iter_topics
from chronos_repro.retrieval import search, fetch_documents, FTS_STOPWORDS
from chronos_repro.date_evidence import explicit_dates
from chronos_repro.frozen_timex import load_topic_annotations


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--trajectory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--sample-count', type=int, default=5)
    args = p.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    trace = json.loads(args.trajectory.read_text(encoding='utf-8'))
    topic = next(t for t in iter_topics(config['data']) if t.topic_id == config['topic'])
    predicted = {e['time'] for e in trace['final_events']}
    references = {}
    for timeline in topic.timelines:
        for day, summaries in timeline.items():
            references.setdefault(day, []).extend(summaries)
    dates = [d for d in sorted(references) if d.isoformat() not in predicted][:args.sample_count]
    annotations = load_topic_annotations(str(Path(config['data']) / config['topic'] / 'articles.preprocessed.jsonl.gz'))
    rows = []
    for day in dates:
        summary = references[day][0]
        terms = list(dict.fromkeys(w for w in re.findall(r'[A-Za-z0-9]+', summary) if w.lower() not in FTS_STOPWORDS))
        query = config['topic'].replace('_', ' ') + ' ' + ' '.join(terms[:16])
        for mode in ('none', 'soft'):
            options = {} if mode == 'none' else {'date_from': (day-timedelta(days=7)).isoformat(),
                'date_to': (day+timedelta(days=7)).isoformat(), 'date_filter_mode': 'soft', 'date_soft_penalty': .5}
            results = search(config['index'], [query], 40, config['dataset'] + ' ' + config['topic'], **options)
            docs = fetch_documents(config['index'], config['topic'], [r['id'] for r in results], 100000)
            matching = []
            for doc in docs:
                values = explicit_dates(doc.get('text', ''))
                values.update(a['date'] for a in annotations.get(str(doc['id']), []))
                if day.isoformat() in values:
                    matching.append(str(doc['id']))
            rows.append({'target_date': day.isoformat(), 'gold_summary_for_offline_diagnosis_only': summary,
                'query': query, 'mode': mode, 'retrieved_documents': len(docs),
                'date_mention_document_ids': matching,
                'event_entailment_reviewed': False})
    report = {'topic': config['topic'], 'role': 'oracle_diagnostic_not_agent_result', 'api_calls': 0,
        'sampling': 'first missing reference dates in chronological order; not a random or representative evaluation',
        'rows': rows, 'limitations': ['Uses gold queries and dates locally only; never combine with autonomous recall',
        'Date mention does not prove the requested event is supported', 'Full-document evidence availability is not bounded reader visibility']}
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(args.output), 'probes': len(rows), 'with_date_mention': sum(bool(r['date_mention_document_ids']) for r in rows), 'api_calls': 0}))


if __name__ == '__main__':
    main()
