import copy
import json

import pytest

from chronos_repro.compact_context import observation_view
from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.evidence_access import split_passages
from chronos_repro.frozen_timex import annotation_rule, article_annotations, attach_annotations
from chronos_repro.llm import DeepSeekClient
from chronos_repro.llm_cache import CachedLLMClient
from chronos_repro.two_phase_state import build_policy_state


def fixture():
    article = {'id': 'p', 'title': 'Aurora', 'text': 'Aurora landed yesterday.', 'time': '2020-01-02T00:00:00',
        'sentences': [{'raw': 'Aurora landed yesterday .', 'tokens': {
            'keys': ['raw', 'time', 'time_format'], 'data': [['Aurora', None, None], ['landed', None, None],
                ['yesterday', '2020-01-01T00:00:00', '%Y-%m-%d'], ['.', None, None]]}}]}
    annotations = list(article_annotations(article))
    passage = split_passages({**article, 'publication_date': article['time']})[0]
    attach_annotations(passage, annotations)
    annotation = annotations[0]
    evidence = {'document_id': passage['id'], 'quote': article['text'], 'time_expression': 'yesterday',
                'annotation_id': annotation['annotation_id']}
    return passage, evidence


def test_frozen_relative_annotation_is_grounded_in_exact_source():
    passage, evidence = fixture()
    checked = validate_date_evidence('2020-01-01', evidence, [passage], [passage['id']])
    assert checked['normalization'] == 'frozen_timex'
    assert checked['publication_anchor'] == '2020-01-02'


@pytest.mark.parametrize('kind', ['wrong_id', 'wrong_date', 'wrong_span', 'wrong_expression', 'wrong_anchor', 'absent'])
def test_frozen_annotation_tampering_is_rejected(kind):
    passage, evidence = fixture()
    target = '2020-01-01'
    if kind == 'wrong_id':
        evidence['annotation_id'] = 'invented'
    elif kind == 'wrong_date':
        target = '2020-01-02'
    elif kind == 'wrong_span':
        passage['temporal_annotations'][0]['source_start'] = 0
    elif kind == 'wrong_expression':
        evidence['time_expression'] = 'landed'
    elif kind == 'wrong_anchor':
        passage['publication_date'] = '2021-01-02'
    else:
        passage.pop('temporal_annotations')
    with pytest.raises(ValueError):
        validate_date_evidence(target, evidence, [passage], [passage['id']])


def test_publication_date_without_annotation_still_cannot_ground_event():
    passage, evidence = fixture()
    evidence.pop('annotation_id')
    with pytest.raises(ValueError):
        validate_date_evidence('2020-01-01', evidence, [passage], [passage['id']])


@pytest.mark.parametrize('expression,target,pub', [('last days', '2020-01-01', '2020-01-02'),
    ('Friday', '2020-01-01', '2020-01-02'), ('yesterday', '2020-01-02', '2020-01-02'),
    ('January', '2020-01-01', '2020-01-02')])
def test_ambiguous_or_inconsistent_normalization_is_rejected(expression, target, pub):
    assert annotation_rule(expression, target, pub) is None


def test_compact_document_keeps_only_required_annotation_fields():
    passage, _ = fixture()
    view = observation_view({'retrieved_documents': [passage]})['retrieved_documents'][0]
    assert view['temporal_annotations'][0]['date'] == '2020-01-01'
    assert 'document_sha256' not in view['temporal_annotations'][0]
    assert view['text'] == passage['text']


def test_controller_gets_bounded_leads_and_public_corpus_profile():
    state = {'events': [], 'corpus_profile': {'publication_year_counts': {'2020': 42}},
             'memory': {'lead_queue': [{'lead_id': str(n), 'status': 'OPEN', 'summary': 'Undated launch'} for n in range(50)]}}
    view = build_policy_state(state, purpose='explore')
    assert len(view['memory']['pending_leads']) == 6
    assert view['memory']['unresolved_lead_count'] == 50
    assert view['corpus_profile'] == state['corpus_profile']


def test_request_options_change_cache_identity(tmp_path):
    from test_llm_cache import Client
    client = Client()
    cache = CachedLLMClient(client, tmp_path)
    cache.chat([])
    client.request_options = {'thinking': {'type': 'disabled'}, 'max_tokens': 8192}
    cache.chat([])
    assert client.calls == 2
    assert cache.chat([]).attempts == 0


def test_bounded_options_reach_http_payload(monkeypatch):
    from test_llm import FakeResponse
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-only')
    bodies = []
    def request(req, **kwargs):
        bodies.append(json.loads(req.data))
        return FakeResponse()
    monkeypatch.setattr('urllib.request.urlopen', request)
    DeepSeekClient(request_options={'thinking': {'type': 'disabled'}, 'max_tokens': 8192}).chat([])
    assert bodies[0]['thinking'] == {'type': 'disabled'}
    assert bodies[0]['max_tokens'] == 8192


def test_request_options_cannot_override_endpoint_or_expose_key():
    with pytest.raises(ValueError):
        DeepSeekClient(request_options={'api_key': 'fixture'})
