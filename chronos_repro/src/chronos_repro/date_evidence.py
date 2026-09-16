"""Conservative literal date grounding; publication metadata is never an anchor.

Unsupported/relative expressions stay provisional rather than being guessed.
This checks date expression consistency, not the semantics of the whole claim.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {name: index for index, names in enumerate([
    ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'),
    ('may',), ('jun', 'june'), ('jul', 'july'), ('aug', 'august'),
    ('sep', 'sept', 'september'), ('oct', 'october'), ('nov', 'november'),
    ('dec', 'december')], 1) for name in names}


def explicit_dates(expression: str) -> set[str]:
    """Return only explicitly written calendar values, at their original precision."""
    text = expression.casefold()
    values = set()
    for match in re.finditer(r'(?<!\d)(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?(?!\d)', text):
        y, m, d = match.groups()
        try:
            date(int(y), int(m or 1), int(d or 1))
            values.add('-'.join(v for v in (y, m, d) if v))
        except ValueError:
            pass
    month_pattern = '|'.join(sorted(MONTHS, key=len, reverse=True))
    patterns = [
        rf'\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{month_pattern})\.?\s*,?\s*(?P<y>\d{{4}})\b',
        rf'\b(?P<m>{month_pattern})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(?P<y>\d{{4}})\b',
        rf'\b(?P<m>{month_pattern})\.?\s+(?P<y>\d{{4}})\b',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            parts = match.groupdict()
            y, m, d = int(parts['y']), MONTHS[parts['m']], parts.get('d')
            try:
                value = date(y, m, int(d or 1))
                values.add(value.isoformat() if d else f'{y:04d}-{m:02d}')
            except ValueError:
                pass
    for match in re.finditer(r'(\d{4})年(\d{1,2})月(?:(\d{1,2})日)?', text):
        y, m, d = match.groups()
        try:
            value = date(int(y), int(m), int(d or 1))
            values.add(value.isoformat() if d else f'{int(y):04d}-{int(m):02d}')
        except ValueError:
            pass
    return values


def validate_date_evidence(event_time: str, evidence: object, documents: list[dict], evidence_ids: list[str]) -> dict:
    if not isinstance(evidence, dict):
        raise ValueError('Date requires date_evidence; never invent a day for a month/year')
    document_id = str(evidence.get('document_id', ''))
    docs = {str(d['id']): d for d in documents}
    if document_id not in docs or document_id not in evidence_ids:
        raise ValueError('date_evidence must cite an available event evidence ID')
    quote, expression = evidence.get('quote'), evidence.get('time_expression')
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 700:
        raise ValueError('date_evidence quote must contain 1-700 characters')
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 100:
        raise ValueError('date_evidence needs a literal time_expression')
    normalize = lambda s: ' '.join(s.split())
    doc = docs[document_id]
    # Source-audited context immediately precedes text, including split words.
    body = str(doc.get('context_before', '')) + str(doc.get('text', ''))
    text = str(doc.get('title', '')) + ' ' + body
    if normalize(quote) not in normalize(text) or normalize(expression) not in normalize(quote):
        raise ValueError('date_evidence quote/expression is not verbatim source text')
    # Cropping the cited expression must not turn an ISO interval endpoint into a point.
    source = normalize(text)
    cited_quote, cited_expression = normalize(quote), normalize(expression)
    interval_pattern = r'(?<!\d)\d{4}-\d{2}-\d{2}\s*(?:to|through|until|[\u2013\u2014\u81f3-])\s*\d{4}-\d{2}-\d{2}(?!\d)'
    intervals = [m.span() for m in re.finditer(interval_pattern, source, re.I)]
    occurrences = []
    for citation in re.finditer('(?=' + re.escape(cited_quote) + ')', source):
        for mention in re.finditer(re.escape(cited_expression), cited_quote):
            start = citation.start() + mention.start()
            occurrences.append((start, start + len(cited_expression)))
    if intervals and occurrences and all(
            any(left <= start and end <= right for left, right in intervals)
            for start, end in occurrences):
        raise ValueError('A cropped date range endpoint cannot ground a point date')
    if evidence.get('annotation_id'):
        from .frozen_timex import annotation_rule
        import hashlib
        annotation = next((a for a in doc.get('temporal_annotations', [])
                           if a['annotation_id'] == evidence['annotation_id']), None)
        if annotation is None or annotation['date'] != event_time or annotation['expression'] != expression:
            raise ValueError('Date annotation must be supplied and match date/expression exactly')
        a, b = annotation['source_start'] - doc['source_start'], annotation['source_end'] - doc['source_start']
        if not 0 <= a < b <= len(doc['text']) or doc['text'][a:b] != expression:
            raise ValueError('Date annotation source offsets do not match the passage')
        digest = doc['document_sha256']
        identity = hashlib.sha256(f"{digest}:{annotation['source_start']}:{annotation['source_end']}:{event_time}".encode()).hexdigest()[:16]
        if identity != annotation['annotation_id'] or annotation['document_sha256'] != digest:
            raise ValueError('Date annotation source identity mismatch')
        published = str(doc.get('publication_date', ''))[:10]
        if annotation['rule'] == 'structured_timeline_year':
            year_anchor = annotation.get('year_anchor', {})
            year = year_anchor.get('year', '')
            if year != event_time[:4] or not year_anchor.get('quote') or year not in year_anchor['quote']:
                raise ValueError('Structured timeline lacks its explicit source year anchor')
            supported = explicit_dates(expression) | explicit_dates(expression + ' ' + year)
            rule = 'structured_timeline_year' if event_time in supported else None
        else:
            rule = annotation_rule(expression, event_time, published)
        if not rule or rule != annotation['rule'] or annotation['publication_anchor'] != published:
            raise ValueError('Date annotation normalization is inconsistent')
        # The event quote must enclose the selected occurrence, not another identical weekday.
        flatten = lambda s: re.sub(r'\s+', '', s)
        offset = len(str(doc.get('title', ''))) + 1 + len(str(doc.get('context_before', ''))) + a
        begin = len(flatten(text[:offset]))
        end = begin + len(flatten(expression))
        cited = flatten(quote)
        if not any(m.start() <= begin and end <= m.start() + len(cited)
                   for m in re.finditer('(?=' + re.escape(cited) + ')', flatten(text))):
            raise ValueError('Date quote does not enclose the annotated source occurrence')
        return {'document_id': document_id, 'quote': quote, 'time_expression': expression,
                'annotation_id': annotation['annotation_id'], 'normalization': 'frozen_timex',
                'annotation_rule': rule, 'publication_anchor': published}
    context = evidence.get('context')
    # A range contains explicit endpoint strings but does not assert a point event.
    range_text = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', 'POINT', expression)
    range_marker = re.search(r'\b(?:to|through|until|between)\b|[\u2013\u2014\u81f3]|\d\s*-\s*\d{1,2}(?!\d|-)|POINT\s*-\s*POINT', range_text, re.I)
    iso_point = re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', expression.strip())
    if range_marker and not iso_point:
        raise ValueError('A date range cannot ground a point date; retain an INSUFFICIENT lead')
    supported = explicit_dates(expression)
    checked_context = None
    if event_time not in supported and isinstance(context, dict):
        if str(context.get('document_id')) != document_id:
            raise ValueError('Contextual year must come from the same supplied passage')
        context_quote = context.get('quote')
        year = str(context.get('year', ''))
        context_text = text + ' ' + str(doc.get('context_before', ''))
        if not isinstance(context_quote, str) or not context_quote.strip() or len(context_quote) > 700 or normalize(context_quote) not in normalize(context_text):
            raise ValueError('Contextual year quote must be verbatim supplied source context')
        years = {v[:4] for v in explicit_dates(context_quote)}
        if not re.fullmatch(r'\d{4}', year) or years != {year}:
            raise ValueError('Context must unambiguously support one literal year')
        supported |= explicit_dates(expression + ' ' + year)
        checked_context = {'document_id': document_id, 'quote': context_quote, 'year': year}
    if event_time not in supported:
        raise ValueError('Event date/precision not supported by time_expression; use INSUFFICIENT with time null or original partial date, never repair to day 01')
    return {'document_id': document_id, 'quote': quote, 'time_expression': expression,
            **({'context': checked_context, 'normalization': 'contextual_year'} if checked_context else {'normalization': 'explicit'})}
