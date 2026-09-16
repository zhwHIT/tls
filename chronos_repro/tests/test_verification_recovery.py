import copy

from chronos_repro.full_timeline import validate_verified_candidates
from chronos_repro.verification_recovery import quarantine_invalid_candidates


def test_invalid_quote_is_quarantined_without_losing_valid_candidate():
    document = {'id': 'p', 'text': 'Aurora launched on 2020-01-01.'}
    valid = {'candidate_id': 'c1', 'status': 'SUPPORTED',
             'event': {'time': '2020-01-01', 'summary': 'Aurora launched its first mission.'},
             'evidence_ids': ['p'], 'confidence': .9, 'relevance_pass': True, 'contribution_pass': True,
             'date_evidence': {'document_id': 'p', 'quote': document['text'], 'time_expression': '2020-01-01'}}
    bad = copy.deepcopy(valid)
    bad['candidate_id'] = 'c2'
    bad['date_evidence']['quote'] = 'Invented quote with 2020-01-01.'
    payload = {'thought': 'Inspect the supplied launch report.', 'candidates': [valid, bad]}
    original = copy.deepcopy(payload)
    result = quarantine_invalid_candidates(payload, [document], 12)
    assert len(result['candidates']) == 1
    assert result['candidates'][0]['candidate_id'] == 'c1'
    validate_verified_candidates(result['candidates'], [document], 12)
    assert result['quarantined_candidates'][0]['raw_candidate'] == bad
    assert not result['extraction_complete'] and not result['training_target']
    assert payload == original


def test_malformed_batch_cannot_create_a_verified_event():
    result = quarantine_invalid_candidates({'candidates': [None, {}]}, [], 12)
    assert result['candidates'] == [] and len(result['quarantined_candidates']) == 2
    assert not result['extraction_complete']
