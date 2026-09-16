"""Source-bound temporal annotations; never infer a date from publication alone."""
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import re

from .date_evidence import explicit_dates

WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'


def structured_timeline_annotations(article):
    """Carry an explicit year between chronological date headings, never publication year."""
    body, current_year, year_anchor, previous = article['text'], None, None, None
    digest = hashlib.sha256(body.encode()).hexdigest()
    pattern = re.compile(r'^(?P<expression>' + MONTH + r'\s+\d{1,2}(?:,?\s+(?P<year>\d{4}))?)\s+[-\u2013\u2014]', re.I)
    offset = 0
    for line in body.splitlines(keepends=True):
        match = pattern.match(line)
        if match:
            expression = match['expression']
            if match['year']:
                current_year = match['year']
                year_anchor = {'year': current_year, 'quote': expression,
                               'source_start': offset, 'source_end': offset + match.end('expression')}
                previous = None
            if current_year:
                values = {v for v in explicit_dates(expression if match['year'] else expression + ' ' + current_year) if len(v) == 10}
                if len(values) == 1:
                    value = values.pop()
                    if previous and value < previous:
                        current_year, year_anchor = None, None  # Ambiguous year rollover needs a new explicit anchor.
                    else:
                        previous = value
                        a, b = offset, offset + match.end('expression')
                        identity = hashlib.sha256(f'{digest}:{a}:{b}:{value}'.encode()).hexdigest()[:16]
                        yield {'annotation_id': identity, 'expression': expression, 'date': value,
                            'source_start': a, 'source_end': b, 'rule': 'structured_timeline_year',
                            'document_sha256': digest, 'publication_anchor': article['time'][:10],
                            'year_anchor': dict(year_anchor)}
        offset += len(line)


def annotation_rule(expression, normalized, published):
    """Whitelist unambiguous lexical forms and check the frozen normalization."""
    target, anchor = date.fromisoformat(normalized), date.fromisoformat(published)
    word = ' '.join(expression.casefold().split())
    if normalized in explicit_dates(expression):
        return 'explicit_frozen_annotation'
    offsets = {'today': 0, 'yesterday': -1, 'the day before yesterday': -2, 'day before yesterday': -2}
    if word in offsets and target == anchor + timedelta(days=offsets[word]):
        return 'anchored_relative_day'
    weekday = re.fullmatch(r'(?:(last|this) )?(' + '|'.join(WEEKDAYS) + r')', word)
    if weekday:
        delta = (anchor - target).days
        minimum = 1 if weekday.group(1) == 'last' else 0
        if target.weekday() == WEEKDAYS.index(weekday.group(2)) and minimum <= delta <= 7:
            return 'anchored_weekday'
    # Missing year may use the frozen annotation only for a literal month and day.
    month = r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?'
    if re.fullmatch(r'(?:' + month + r' \d{1,2}(?:st|nd|rd|th)?|\d{1,2}(?:st|nd|rd|th)? ' + month + r')', word):
        if normalized in explicit_dates(expression + ' ' + str(target.year)) and 0 <= (anchor - target).days <= 183:
            return 'anchored_month_day'
    return None


def article_annotations(article):
    body = article['text']
    structured = list(structured_timeline_annotations(article))
    yield from structured
    structured_spans = {(r['source_start'], r['source_end']) for r in structured}
    joined = re.sub(r'\s+', '', body)
    positions = [i for i, c in enumerate(body) if not c.isspace()]
    digest = hashlib.sha256(body.encode()).hexdigest()
    cursor = 0
    for sentence in article.get('sentences', []):
        tokens = [dict(zip(sentence['tokens']['keys'], t)) for t in sentence['tokens']['data']]
        raw = re.sub(r'\s+', '', sentence['raw'])
        start = joined.find(raw, cursor)
        if start < 0:
            start = joined.find(raw)
        if start < 0 or not raw:
            continue
        cursor = start + len(raw)
        token_cursor, index = 0, 0
        while index < len(tokens):
            token = tokens[index]
            spelling = re.sub(r'\s+', '', token['raw'])
            left = raw.find(spelling, token_cursor)
            if left < 0 or not spelling:
                index += 1
                continue
            right = left + len(spelling)
            token_cursor = right
            if token.get('time_format') != '%Y-%m-%d' or not token.get('time'):
                index += 1
                continue
            following = index + 1
            while following < len(tokens) and tokens[following].get('time') == token['time']:
                value = re.sub(r'\s+', '', tokens[following]['raw'])
                found = raw.find(value, token_cursor)
                if found < 0 or not value:
                    break
                right = found + len(value)
                token_cursor = right
                following += 1
            index = following
            a, b = positions[start + left], positions[start + right - 1] + 1
            expression, normalized, published = body[a:b], token['time'][:10], article['time'][:10]
            if (a, b) in structured_spans:
                continue
            try:
                rule = annotation_rule(expression, normalized, published)
            except ValueError:
                continue
            if not rule or len(expression) > 100:
                continue
            identity = hashlib.sha256(f'{digest}:{a}:{b}:{normalized}'.encode()).hexdigest()[:16]
            yield {'annotation_id': identity, 'expression': expression, 'date': normalized,
                   'source_start': a, 'source_end': b, 'rule': rule, 'document_sha256': digest,
                   'publication_anchor': published}


@lru_cache(maxsize=4)
def load_topic_annotations(path):
    output = defaultdict(list)
    with gzip.open(Path(path), 'rt', encoding='utf-8') as source:
        for line in source:
            article = json.loads(line)
            output[str(article['id'])].extend(article_annotations(article))
    return dict(output)


def attach_annotations(passage, annotations, maximum=12, represented_dates=()):
    # Compact source IDs/date/spans; the event quote still comes from supplied body.
    rows = [row for row in annotations if row['document_sha256'] == passage['document_sha256']
            and passage['source_start'] <= row['source_start'] < row['source_end'] <= passage['source_end']]
    known = set(represented_dates)
    rows.sort(key=lambda row: (row['date'] in known, row['source_start'], row['source_end']))
    passage['temporal_annotations'] = [dict(row) for row in rows[:maximum]]
    for row in passage['temporal_annotations']:
        a, b = row['source_start'] - passage['source_start'], row['source_end'] - passage['source_start']
        padding = max(0, (180 - (b - a)) // 2)
        row['source_quote'] = passage['text'][max(0, a - padding):min(len(passage['text']), b + padding)]
    passage['temporal_annotations_omitted'] = max(0, len(rows) - maximum)
    return passage
