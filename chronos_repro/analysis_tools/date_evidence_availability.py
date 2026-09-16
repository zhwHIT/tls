"""Offline date-expression availability, not an event coverage score or strict upper bound."""
import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.date_evidence import explicit_dates
from chronos_repro.frozen_timex import article_annotations


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    topic = next(t for t in iter_topics(config['data']) if t.topic_id == config['topic'])
    gold = {d.isoformat() for t in topic.timelines for d in t}
    literal, annotated, published = defaultdict(list), defaultdict(list), defaultdict(list)
    count = 0
    with gzip.open(Path(config['data']) / config['topic'] / 'articles.preprocessed.jsonl.gz', 'rt', encoding='utf-8') as f:
        for line in f:
            article = json.loads(line)
            identity = str(article['id'])
            count += 1
            for day in gold & explicit_dates(article.get('title', '') + '\n' + article['text']):
                literal[day].append(identity)
            for day in gold & {r['date'] for r in article_annotations(article)}:
                annotated[day].append(identity)
            day = str(article['time'])[:10]
            if day in gold:
                published[day].append(identity)
    rows = [{'date': d, 'literal_document_count': len(literal[d]),
        'annotated_document_count': len(annotated[d]), 'publication_document_count': len(published[d]),
        'literal_examples': literal[d][:3], 'annotation_examples': annotated[d][:3]} for d in sorted(gold)]
    report = {'topic': config['topic'], 'documents_checked': count, 'reference_dates': len(gold),
        'rows': rows, 'api_calls': 0,
        'limits': ['A date mention need not refer to the gold event; no semantic verification performed',
            'Absence from these parsers does not prove the event/date cannot be recovered by another valid normalization',
            'Publication dates are diagnostic metadata only, never counted as event evidence here']}
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, indent=2))
