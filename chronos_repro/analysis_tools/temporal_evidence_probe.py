"""Offline probe of frozen timex metadata omitted by the retrieval reader."""
import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import json
from pathlib import Path
import re

from chronos_repro.data import iter_topics


def compact(text):
    return re.sub(r'\s+', '', text)


def normalized_mentions(article):
    body = article['text']
    compact_body = compact(body)
    positions = [i for i, c in enumerate(body) if not c.isspace()]
    cursor = 0
    for sentence in article.get('sentences', []):
        tokens = [dict(zip(sentence['tokens']['keys'], t)) for t in sentence['tokens']['data']]
        sentence_compact = compact(sentence['raw'])
        start = compact_body.find(sentence_compact, cursor)
        if start < 0:
            start = compact_body.find(sentence_compact)
        if start < 0:
            continue
        cursor = start + len(sentence_compact)
        token_cursor = 0
        index = 0
        while index < len(tokens):
            token = tokens[index]
            token_start = sentence_compact.find(compact(token['raw']), token_cursor)
            if token_start < 0:
                index += 1
                continue
            token_end = token_start + len(compact(token['raw']))
            token_cursor = token_end
            if token.get('time_format') != '%Y-%m-%d' or not token.get('time'):
                index += 1
                continue
            stamp = token['time'][:10]
            following = index + 1
            while following < len(tokens) and tokens[following].get('time') == token['time']:
                end = sentence_compact.find(compact(tokens[following]['raw']), token_cursor)
                if end < 0:
                    break
                token_cursor = end + len(compact(tokens[following]['raw']))
                token_end = token_cursor
                following += 1
            index = following
            original_start = positions[start + token_start]
            original_end = positions[start + token_end - 1] + 1
            expression = body[original_start:original_end]
            try:
                normalized = date.fromisoformat(stamp)
                publication = date.fromisoformat(article['time'][:10])
            except ValueError:
                continue
            label = 'other_normalized_annotation'
            word = expression.strip().casefold()
            if word in {'today', 'yesterday', 'tomorrow'}:
                shift = {'today': 0, 'yesterday': -1, 'tomorrow': 1}[word]
                label = 'relative_day_consistent' if normalized == publication + timedelta(days=shift) else 'relative_day_conflict'
            elif word in {x.casefold() for x in ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')}:
                delta = (publication - normalized).days
                label = 'weekday_consistent' if normalized.strftime('%A').casefold() == word and 0 <= delta <= 7 else 'weekday_conflict_or_future'
            yield {'document_id': str(article['id']), 'start': original_start, 'end': original_end,
                   'expression': expression, 'normalized_date': stamp, 'publication_date': article['time'][:10],
                   'kind': label, 'source': 'frozen_token_time_annotation',
                   'quote': body[max(0, original_start - 120):min(len(body), original_end + 160)]}


def probe(project, config_path, trajectory_path):
    config = json.loads(config_path.read_text(encoding='utf-8'))
    trace = json.loads(trajectory_path.read_text(encoding='utf-8'))
    passages = {d['id']: d for s in trace['steps'] if s['action'] == 'SEARCH'
                for d in s.get('observation', {}).get('documents', [])}
    by_parent = defaultdict(list)
    for passage in passages.values():
        by_parent[passage['document_id']].append(passage)
    topic = next(t for t in iter_topics(project / config['data']) if t.topic_id == config['topic'])
    gold = {d.isoformat() for timeline in topic.timelines for d in timeline}
    rows = []
    path = project / config['data'] / config['topic'] / 'articles.preprocessed.jsonl.gz'
    with gzip.open(path, 'rt', encoding='utf-8') as source:
        for line in source:
            article = json.loads(line)
            if str(article['id']) not in by_parent:
                continue
            for mention in normalized_mentions(article):
                for passage in by_parent[str(article['id'])]:
                    if passage['source_start'] <= mention['start'] and mention['end'] <= passage['source_end']:
                        rows.append({**mention, 'passage_id': passage['id']})
    values = {r['normalized_date'] for r in rows}
    consistent = {r['normalized_date'] for r in rows if r['kind'] in {'relative_day_consistent','weekday_consistent'}}
    return {'topic': config['topic'], 'mentions': len(rows), 'kinds': dict(Counter(r['kind'] for r in rows)),
            'all_annotation_date_hits': len(gold & values), 'relative_consistent_date_hits': len(gold & consistent),
            'gold_date_count': len(gold), 'examples': rows[:12], 'api_calls': 0,
            'note': 'Date availability diagnostic only; does not establish an event or semantic coverage'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--trajectory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = probe(args.project_root, args.config, args.trajectory)
    content = json.dumps(report, ensure_ascii=False, indent=2)
    with args.output.open('x', encoding='utf-8') as target:
        target.write(content + '\n')
    print(content)
