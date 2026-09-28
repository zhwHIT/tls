"""Reject source-local contradictions; never invent replacement event dates."""
from datetime import date
import re

_MONTHS = ('january february march april may june july august september october november december').split()
_MONTH = '(?:' + '|'.join(m + '|' + m[:3] + r'\.?' for m in _MONTHS) + ')'
_MONTH_DAY = re.compile(r'(?P<month>' + _MONTH + r')\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?'
                        r'(?:,?\s+(?P<year>\d{4}))?', re.I)
_DAY_MONTH = re.compile(r'(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>' + _MONTH + r')'
                        r'(?:,?\s+(?P<year>\d{4}))?', re.I)
_WEEKDAY = re.compile(r'(?:(?:last|this)\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)', re.I)


def annotation_context_conflict(text, start, end, normalized, published):
    """Inspect the selected weekday occurrence, not an unrelated date in the article."""
    if not _WEEKDAY.fullmatch(text[start:end].strip()):
        return None
    target, anchor = date.fromisoformat(normalized), date.fromisoformat(published)
    after = text[end:]
    after = re.sub(r'^\s*,?\s*', '', after)
    matches = [p.match(after) for p in (_MONTH_DAY, _DAY_MONTH)]
    # Also support 'June 21, Thursday' immediately before this occurrence.
    before = text[:start]
    for pattern in (_MONTH_DAY, _DAY_MONTH):
        matches.extend(m for m in pattern.finditer(before[-80:])
                       if re.fullmatch(r'\s*,?\s*', before[-80:][m.end():]))
    for match in filter(None, matches):
        month = next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(match['month'].lower().rstrip('.')))
        if (target.month, target.day) != (month, int(match['day'])) or (
                match['year'] and target.year != int(match['year'])):
            return 'weekday_annotation_conflicts_with_adjacent_explicit_date'
    clause = re.split(r'[.!?;\n]', before)[-1][-160:]
    if target < anchor and re.search(
            r'\b(?:set for|scheduled (?:for|on)|due (?:on|to)|will|upcoming|next|expected (?:on|to))\b',
            clause, re.I):
        return 'past_weekday_annotation_in_future_context'
    return None
