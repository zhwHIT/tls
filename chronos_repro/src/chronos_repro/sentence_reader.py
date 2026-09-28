"""Online sentence union and persistent unread candidates; no reference labels.

The three-lane policy matches the offline DATEWISE-inspired experiment. Budgets
come from the legacy reader on the current query, rather than a saved trajectory.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from functools import lru_cache

from tokenizers import Tokenizer
from .evidence_access import EvidenceReader, _passage, terms
from .retrieval import _bm25_path


DATE_CUE = re.compile(
    r'\b(?:19|20)\d{2}\b|\b(?:today|yesterday|tomorrow|last night)\b|'
    r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|'
    r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b', re.I)
LANES = ('original_related', 'opening_main_candidate', 'date_mention')


def merge_ranges(ranges):
    out = []
    for a, b in sorted(set(map(tuple, ranges))):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def contains(ranges, a, b):
    return any(x <= a and y >= b for x, y in merge_ranges(ranges))


def sentence_candidates(doc):
    units, pending = [], None
    for m in re.finditer(r'[^\n]+', doc['text']):
        raw = m.group()
        a = m.start() + len(raw) - len(raw.lstrip())
        b = m.end() - len(raw) + len(raw.rstrip())
        if a >= b:
            continue
        if pending is not None:
            a, pending = pending, None
        if len(doc['text'][a:b].split()) < 4:
            pending = a
            continue
        units.append((a, b))
    if pending is not None:
        if units:
            units[-1] = (units[-1][0], len(doc['text'].rstrip()))
        else:
            units = [(pending, len(doc['text'].rstrip()))]
    result = []
    for n, (a, b) in enumerate(units):
        text, start = doc['text'][a:b], a
        if n:
            prior = doc['text'][units[n-1][0]:units[n-1][1]]
            if re.match(r'^(?:He|She|It|They|His|Her|Their|This|That|These|Those)\b', text, re.I) or not re.search(r'[.!?][\s\"\u201d\u2019]*$', prior):
                start = units[n-1][0]
        result.append({'id': f'{doc["id"]}:{a}:{b}', 'document_id': doc['id'],
                       'core_start': a, 'core_end': b, 'start': start, 'end': b,
                       'ordinal': n, 'opening': n < 5, 'has_date': bool(DATE_CUE.search(text)),
                       'terms': terms(text), 'title_terms': terms(doc['title'])})
    return result


class SentenceUnionReader(EvidenceReader):
    def __init__(self, index, topic, settings):
        super().__init__(index, topic, settings)
        self.baseline = EvidenceReader(index, topic, settings)
        self.tokenizer = Tokenizer.from_file(settings['sentence_tokenizer_path'])
        self.documents, self.pending, self.first_seen = {}, {}, {}
        self.visible_ranges = defaultdict(list)
        self.best_ranks, self.budget_skips = {}, Counter()
        self.selection_history = []
        self.current_ids = set()
        self.query_number = 0

    def add_results(self, results):
        self.baseline.add_results(results)
        self.current_ids = {str(r['id']) for r in results}
        for rank, r in enumerate(results, 1):
            did = str(r['id'])
            self.best_ranks[did] = min(rank, self.best_ranks.get(did, rank))
        wanted = self.current_ids - self.loaded_documents
        if not wanted:
            return
        placeholders = ','.join('?' for _ in wanted)
        with sqlite3.connect(_bm25_path(self.index).resolve().as_uri() + '?mode=ro', uri=True) as con:
            rows = con.execute(f'SELECT doc_id,title,text,timestamp FROM documents WHERE topic=? AND doc_id IN ({placeholders})',
                               [self.topic, *sorted(wanted)]).fetchall()
        for did, title, text, timestamp in rows:
            did = str(did)
            doc = {'id': did, 'title': title, 'text': text, 'publication_date': timestamp}
            self.documents[did] = doc
            self.first_seen[did] = self.query_number + 1
            self.pending.update({c['id']: c for c in sentence_candidates(doc)})
            self.loaded_documents.add(did)
        self.temporal_annotations = self.baseline.temporal_annotations

    def payload(self, ranges):
        out = []
        for did in sorted(ranges):
            d = self.documents[did]
            out.append({'document_id': did, 'document_sha256': hashlib.sha256(d['text'].encode()).hexdigest(),
                        'title': d['title'], 'publication_date': d['publication_date'],
                        'segments': [{'source_start': a, 'source_end': b, 'text': d['text'][a:b]}
                                     for a, b in merge_ranges(ranges[did])]})
        return out

    @lru_cache(maxsize=16384)
    def encoded(self, wire):
        return len(self.tokenizer.encode(wire, add_special_tokens=False).ids)

    def cost(self, ranges):
        payload = self.payload(ranges)
        return (self.encoded(json.dumps(payload, ensure_ascii=False, separators=(',', ':'))),
                sum(len(s['text']) for d in payload for s in d['segments']))

    def select(self, query, limit=None, events=(), temporal=None):
        temporal = temporal or {}
        if temporal.get('event_scope'):
            raise ValueError('Sentence reader is scoped to fresh first-phase runs, not interval supplementation')
        self.query_number += 1
        # Retire a legacy passage only when its complete visible source interval was shown.
        for key, p in self.baseline.passages.items():
            if contains(self.visible_ranges[p['document_id']], p['source_start']-len(p['context_before']), p['source_end']):
                self.baseline.processed.add(key)
        baseline = self.baseline.select(query, limit=limit, events=events, temporal=temporal)
        original = defaultdict(list)
        for p in baseline:
            original[p['document_id']].append([p['source_start']-len(p['context_before']), p['source_end']])
        budget_tokens, budget_chars = self.cost(original)
        from .temporal_filter import in_publication_range
        mode = temporal.get('date_filter_mode', 'none')
        def inside(c):
            return in_publication_range(self.documents[c['document_id']]['publication_date'], temporal.get('date_from'), temporal.get('date_to'))
        pool = [c for c in self.pending.values() if mode != 'hard' or inside(c)]
        selected, chosen, covered_terms = {}, [], []
        qt, topic = terms(query), terms(self.topic.replace('_', ' '))
        def sim(a, b):
            return len(a & b)/max(1, len(a | b))
        def eligible(c, lane):
            if lane == 'original_related':
                return contains(original[c['document_id']], c['core_start'], c['core_end'])
            return c['opening'] if lane == 'opening_main_candidate' else c['has_date']
        def score(c, lane):
            t = c['terms']
            q, top = len(t & qt)/max(1, len(qt)), len(t & topic)/max(1, len(topic))
            rank = 1/self.best_ranks.get(c['document_id'], 12)
            if lane == 'original_related':
                s = .5*q + .25*c['has_date'] + .15*top + .1/(1+c['ordinal'])
            elif lane == 'opening_main_candidate':
                s = .45*top + .30*sim(t, c['title_terms']) + .15*q + .10*rank
            else:
                s = .50*q + .20*top + .20*c['has_date'] + .10*rank
            age = min(self.query_number-self.first_seen[c['document_id']], 8)/8
            redundancy = max((sim(t, x) for x in covered_terms), default=0)
            penalty = temporal.get('date_soft_penalty', .5) if mode == 'soft' and not inside(c) else 0
            return (s+.10*age-.25*redundancy-penalty, -self.first_seen[c['document_id']], -c['ordinal'], c['id'])
        def add_one(lane, ceiling):
            rows = [c for c in pool if eligible(c, lane) and not contains(selected.get(c['document_id'], []), c['core_start'], c['core_end'])]
            for c in sorted(rows, key=lambda c: score(c, lane), reverse=True):
                proposal = {i: list(v) for i, v in selected.items()}
                proposal.setdefault(c['document_id'], []).append([c['start'], c['end']])
                tokens, chars = self.cost(proposal)
                if tokens > ceiling or chars > budget_chars:
                    self.budget_skips[c['id']] += 1
                    continue
                selected.clear()
                selected.update(proposal)
                chosen.append({'candidate_id': c['id'], 'lane': lane,
                               'from_previous_query': self.first_seen[c['document_id']] < self.query_number,
                               'outside_current_top12': c['document_id'] not in self.current_ids})
                covered_terms.append(c['terms'])
                return True
            return False
        for lane, fraction in zip(LANES, (.45, .75, 1.0)):
            while add_one(lane, int(budget_tokens*fraction)):
                pass
        while True:
            changed = False
            for lane in LANES:
                if add_one(lane, budget_tokens):
                    changed = True
            if not changed:
                break
        docs = []
        for did, ranges in selected.items():
            self.visible_ranges[did] = merge_ranges(self.visible_ranges[did] + ranges)
            for a, b in merge_ranges(ranges):
                p = _passage(self.documents[did], self.documents[did]['text'], a, b)
                # The selected interval already includes the chosen context; no free 400-character prefix.
                p['context_before'] = ''
                if self.temporal_annotations is not None:
                    from .frozen_timex import attach_annotations
                    attach_annotations(p, self.temporal_annotations.get(did, []), represented_dates={e['time'] for e in events})
                self.passages[p['id']] = p
                docs.append(dict(p))
        self.pending = {k: c for k, c in self.pending.items()
                        if not contains(self.visible_ranges[c['document_id']], c['core_start'], c['core_end'])}
        tokens, chars = self.cost(selected)
        assert tokens <= budget_tokens and chars <= budget_chars
        record = {'query_number': self.query_number, 'query': query, 'current_result_ids': sorted(self.current_ids),
                  'baseline_passage_ids': [p['id'] for p in baseline],
                  'token_budget': budget_tokens, 'visible_char_budget': budget_chars,
                  'tokens': tokens, 'visible_chars': chars, 'payload': self.payload(selected), 'choices': chosen,
                  'passage_ids': [p['id'] for p in docs], 'unread_candidate_count': len(self.pending)}
        self.selection_history.append(record)
        return docs

    def summary(self):
        return {**super().summary(), 'unread_sentence_candidate_count': len(self.pending),
                'selected_source_document_count': len([r for r in self.visible_ranges.values() if r]),
                'selection_policy': 'sentence_union_with_queue_v1'}

    def manifest(self):
        return {**super().manifest(), 'selection_history': self.selection_history,
                'unread_candidates': [{k: v for k, v in c.items() if k not in ('terms', 'title_terms')} |
                                      {'first_seen_query': self.first_seen[c['document_id']], 'budget_skip_attempts': self.budget_skips[c['id']]}
                                      for c in self.pending.values()],
                'visible_ranges': dict(self.visible_ranges),
                'queue_semantics': 'Selected source intervals are reserved across queries; processed passage IDs require successful complete extraction. Incomplete extraction remains in the runtime checkpoint.'}
