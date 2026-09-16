"""Query-aware continuous passage access over frozen documents (no model calls)."""
from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from collections import Counter

from .retrieval import _bm25_path, FTS_STOPWORDS


def terms(text: str) -> set[str]:
    return set(re.findall(r'[^\W_]+', text.casefold())) - FTS_STOPWORDS


def split_passages(document: dict, max_words: int = 500, max_chars: int = 3200, overlap_words: int = 60) -> list[dict]:
    """Offsets refer to the exact frozen body; never stitch distant text together."""
    if max_words < 20 or max_chars < 200 or not 0 <= overlap_words < max_words:
        raise ValueError('Invalid passage limits')
    body = str(document.get('text') or '')
    tokens = list(re.finditer(r'\S+', body))
    output, begin = [], 0
    while begin < len(tokens):
        start = tokens[begin].start()
        end_index = min(len(tokens), begin + max_words)
        while end_index > begin + 1 and tokens[end_index - 1].end() - start > max_chars:
            end_index -= 1
        end = min(tokens[end_index - 1].end(), start + max_chars)
        # A pathological no-whitespace span is also fully covered, not silently truncated.
        if end_index == begin + 1 and tokens[begin].end() > end:
            while start < tokens[begin].end():
                stop = min(start + max_chars, tokens[begin].end())
                output.append(_passage(document, body, start, stop))
                start = stop
        else:
            output.append(_passage(document, body, start, end))
        if end_index == len(tokens):
            break
        begin = max(begin + 1, end_index - overlap_words)
    return output


def _passage(document, body, start, end):
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    doc_id = str(document['id'])
    return {'id': f'{doc_id}@{digest[:12]}:{start}:{end}', 'document_id': doc_id,
            'title': str(document.get('title') or ''), 'text': body[start:end],
            'publication_date': str(document.get('publication_date', ''))[:10],
            'context_before': body[max(0, start - 400):start],
            'source_start': start, 'source_end': end, 'document_sha256': digest,
            'content_sha256': hashlib.sha256(body[start:end].encode('utf-8')).hexdigest()}


class EvidenceReader:
    def __init__(self, index, topic, settings):
        self.index, self.topic, self.settings = index, topic, settings
        self.passages = {}
        self.loaded_documents = set()
        self.processed = set()
        self.retrieval_ranks = {}
        self.temporal_annotations = None

    def add_results(self, results):
        ranks = {str(r['id']): n + 1 for n, r in enumerate(results)}
        self.retrieval_ranks.update(ranks)
        wanted = set(ranks) - self.loaded_documents
        if not wanted:
            return
        # One read per result set, not an expensive full-table scan per document.
        placeholders = ','.join('?' for _ in wanted)
        uri = _bm25_path(self.index).resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute(
                f'SELECT doc_id,title,text,timestamp FROM documents WHERE topic=? AND doc_id IN ({placeholders})',
                [self.topic, *sorted(wanted)]).fetchall()
        for doc_id, title, text, timestamp in rows:
            document = {'id': str(doc_id), 'title': title, 'text': text, 'publication_date': timestamp}
            for passage in split_passages(document, self.settings.get('passage_words', 500),
                                          self.settings.get('passage_chars', 3200),
                                          self.settings.get('overlap_words', 60)):
                if self.settings.get('frozen_topic_path'):
                    from .frozen_timex import load_topic_annotations, attach_annotations
                    if self.temporal_annotations is None:
                        self.temporal_annotations = load_topic_annotations(self.settings['frozen_topic_path'])
                    attach_annotations(passage, self.temporal_annotations.get(str(doc_id), []))
                self.passages[passage['id']] = passage
            self.loaded_documents.add(str(doc_id))

    def select(self, query, limit=None, events=(), temporal=None):
        limit = self.settings.get('passages_per_search', 12) if limit is None else limit
        if limit < 1:
            raise ValueError('Passage selection limit must be positive')
        q = terms(query)
        candidates = [p for key, p in self.passages.items() if key not in self.processed]
        if self.temporal_annotations is not None:
            from .frozen_timex import attach_annotations
            represented = {e['time'] for e in events}
            for passage in candidates:
                attach_annotations(passage, self.temporal_annotations.get(passage['document_id'], []),
                                   represented_dates=represented)
        from .temporal_filter import in_publication_range
        temporal = temporal or {}
        mode = temporal.get('date_filter_mode', 'none')
        inside = lambda p: in_publication_range(p.get('publication_date'), temporal.get('date_from'), temporal.get('date_to'))
        if mode == 'hard':
            candidates = [p for p in candidates if inside(p)]
        # Local IDF over the candidate passage pool, plus document recall rank.
        token_sets = {p['id']: terms(p['title'] + ' ' + p['text']) for p in candidates}
        df = Counter(t for tokens in token_sets.values() for t in tokens)
        idf = {t: math.log(1 + len(candidates) / (1 + df[t])) for t in q}
        represented_years = Counter(e['time'][:4] for e in events)
        def coverage_bonus(p):
            years = set(re.findall(r'\b(?:19|20)\d{2}\b', p['text']))
            return max((0.08 / (1 + represented_years[y]) for y in years), default=0)
        scores = {p['id']: sum(idf[t] for t in q & token_sets[p['id']]) / max(1, sum(idf.values()))
                  + coverage_bonus(p)
                  + 0.1 / self.retrieval_ranks.get(p['document_id'], 100)
                  - (temporal.get('date_soft_penalty', 0.5) if mode == 'soft' and not inside(p) else 0)
                  for p in candidates}
        selected, counts, hashes = [], Counter(), set()
        represented_dates = {e['time'] for e in events}
        while candidates and len(selected) < limit:
            def score(p):
                # Soft document diversity, not a permanent per-document exclusion.
                overlap = max((len(token_sets[p['id']] & token_sets[s['id']]) /
                               max(1, len(token_sets[p['id']] | token_sets[s['id']]))) for s in selected) if selected else 0
                selected_dates = {a['date'] for s in selected for a in s.get('temporal_annotations', [])}
                novel_dates = {a['date'] for a in p.get('temporal_annotations', [])} - represented_dates - selected_dates
                temporal_gain = 0.12 * min(2, len(novel_dates)) if q & token_sets[p['id']] else 0
                return (scores[p['id']] + temporal_gain - 0.15 * counts[p['document_id']] - 0.2 * overlap, p['id'])
            best = max(candidates, key=score)
            candidates.remove(best)
            if best['content_sha256'] in hashes:
                continue
            hashes.add(best['content_sha256'])
            selected.append(dict(best, reader_score=round(scores[best['id']], 6)))
            if mode != 'none':
                selected[-1]['reader_date_filter'] = {'mode': mode, 'in_range': inside(best),
                    'date_from': temporal.get('date_from'), 'date_to': temporal.get('date_to'),
                    'semantics': 'publication_date_not_event_date'}
            counts[best['document_id']] += 1
        return selected

    def mark_processed(self, passages):
        # Called only after successful verification, never on initial selection.
        for p in passages:
            if p['id'] not in self.passages:
                raise ValueError('Unknown passage cannot be marked processed')
            self.processed.add(p['id'])

    def summary(self):
        return {'known_document_count': len(self.loaded_documents), 'known_passage_count': len(self.passages),
                'processed_passage_count': len(self.processed),
                'unread_passage_count': len(self.passages) - len(self.processed)}

    def manifest(self):
        return {'summary': self.summary(), 'passages': list(self.passages.values()),
                'processed_passage_ids': sorted(self.processed)}


def related_events(events, candidates, limit=40):
    dates = {c['event'].get('time') for c in candidates}
    query = set().union(*(terms(c['event']['summary']) for c in candidates)) if candidates else set()
    # Preserve all same-date events to avoid hiding duplicate UPDATE targets.
    same = [e for e in events if e['time'] in dates]
    others = sorted((e for e in events if e['time'] not in dates),
                    key=lambda e: (-len(terms(e['summary']) & query), e['event_id']))
    return sorted(same + others[:max(0, limit - len(same))], key=lambda e: (e['time'], e['event_id']))
