import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
a = importlib.import_module('apply_llm_tls_assistant_corrections')


def test_example_cannot_supply_correction_evidence():
    row = {'job_id': 'j', 'messages': json.dumps([{'content':
           'example-only evidence\n### Content 2\ntarget text\n### Event Related to test'}])}
    item = {'response': '2012: An event.', 'evidence': 'example-only evidence'}
    with pytest.raises(ValueError, match='Evidence missing'):
        a.prepare_result(row, item, 'batch')


def test_range_preserves_unknown_single_date_and_assistant_provenance():
    row = {'job_id': 'j', 'messages': json.dumps([{'content':
           '### Content 2\nBetween 2001 and 2009 schools were damaged.\n### Event Related to test'}])}
    item = {'response': 'UNKNOWN: Schools were damaged between 2001 and 2009.',
            'evidence': 'Between 2001 and 2009 schools were damaged.',
            'time_range': {'start': '2001', 'end': '2009', 'precision': 'year'}}
    result = a.prepare_result(row, item, 'batch')
    event = result['events'][0]
    assert event['event_date'] is None and event['time_range'] == item['time_range']
    assert event['origin'] == 'conversation_assistant'
    assert result['attempts'] == 0 and result['usage'] == {}


def test_report_adjusts_article_mappings_without_double_counting_requests():
    base = {'job_counts': {'done': 1, 'error': 1},
            'parse_status_counts': {'parse_error': 1}, 'unique_generated_events': 1,
            'usage': {'total_tokens': 9},
            'topics': [{'dataset': 'd', 'topic': 't', 'completed_articles': 2, 'error_articles': 1}]}
    rows = [{'job_id': 'a', 'status': 'done', 'result': json.dumps({
                'parse_status': 'parse_error', 'events': [{}], 'usage': {'total_tokens': 9}})},
            {'job_id': 'b', 'status': 'error', 'result': None}]
    results = {'a': {'parse_status': 'no_events', 'events': []},
               'b': {'parse_status': 'extracted', 'events': [{}]}}
    report = a.adjusted_report(base, rows, results, {'a': [('d','t'), ('d','t')], 'b': [('d','t')]})
    assert report['job_counts'] == {'done': 2}
    assert report['parse_status_counts'] == {'extracted': 1, 'no_events': 1}
    assert report['unique_generated_events'] == 1
    assert report['topics'][0]['completed_articles'] == 3
    assert report['topics'][0]['error_articles'] == 0
    assert report['usage']['total_tokens'] == 0
    assert base['job_counts']['error'] == 1
