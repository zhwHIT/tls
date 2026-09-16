import hashlib

import pytest

from chronos_repro.evidence_access import split_passages
from chronos_repro.frozen_timex import structured_timeline_annotations, attach_annotations
from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.exploration_memory import initial_memory, validate_memory_output


def test_retrospective_timeline_uses_explicit_year_not_publication_year():
    article = {'id': 'p', 'text': 'January 29, 2011 - Aurora begins.\nMarch 2 - Aurora launches.\nJanuary 22, 2012 - Aurora returns.',
               'time': '2012-02-20', 'title': 'Timeline'}
    rows = list(structured_timeline_annotations(article))
    assert [r['date'] for r in rows] == ['2011-01-29', '2011-03-02', '2012-01-22']
    passage = split_passages({**article, 'publication_date': article['time']})[0]
    attach_annotations(passage, rows)
    row = rows[1]
    checked = validate_date_evidence(row['date'], {'document_id': passage['id'], 'time_expression': row['expression'],
        'annotation_id': row['annotation_id'], 'quote': 'March 2 - Aurora launches.'}, [passage], [passage['id']])
    assert checked['annotation_rule'] == 'structured_timeline_year'


def test_year_rollover_is_not_guessed():
    article = {'id': 'p', 'text': 'December 20, 2011 - Aurora begins.\nJanuary 2 - Aurora launches.', 'time': '2012-02-20'}
    assert [r['date'] for r in structured_timeline_annotations(article)] == ['2011-12-20']


def test_annotation_window_moves_to_dates_not_yet_represented():
    article = {'id': 'p', 'text': 'January 1, 2011 - Aurora begins.\nMarch 2 - Aurora launches.\nApril 3 - Aurora lands.', 'time': '2012-02-20'}
    rows = list(structured_timeline_annotations(article))
    passage = split_passages({**article, 'publication_date': article['time']})[0]
    attach_annotations(passage, rows, maximum=1, represented_dates={'2011-01-01'})
    assert passage['temporal_annotations'][0]['date'] == '2011-03-02'
    assert len(passage['temporal_annotations']) == 1


def test_month_day_without_source_year_cannot_be_a_structured_anchor():
    assert list(structured_timeline_annotations({'id': 'p', 'text': 'March 2 - Aurora launches.', 'time': '2012-02-20'})) == []


def test_memory_event_citation_resolves_to_existing_provenance():
    from test_exploration_memory import output, DOC
    memory = initial_memory([])
    memory['verified_event_sources'] = {'event-1': ['d1']}
    payload = output()
    payload['stage_outline'][0]['evidence_ids'] = ['event-1']
    checked = validate_memory_output(payload, memory, [DOC])
    assert checked['stage_outline'][0]['evidence_ids'] == ['d1']
    assert payload['stage_outline'][0]['evidence_ids'] == ['event-1']
    payload['stage_outline'][0]['evidence_ids'] = ['event-unknown']
    with pytest.raises(ValueError):
        validate_memory_output(payload, memory, [DOC])
