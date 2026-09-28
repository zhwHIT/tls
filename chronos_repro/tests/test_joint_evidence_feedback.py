import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chronos_repro import batch_memory
from chronos_repro.batch_policy import validate_action
from chronos_repro.compact_context import compact_instruction
from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.evidence_access import _passage
from chronos_repro.joint_verification import expand_batch, record_resolutions
from chronos_repro.lead_feedback import record_presentation
from chronos_repro.llm import ChatResult
from chronos_repro.verify_partial_repair import _checked


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import coverage_pipeline
    return coverage_pipeline


def candidate(key='old', summary='Mira announced her retirement from athletics.'):
    return {'candidate_id': key, 'status': 'INSUFFICIENT',
            'event': {'time': None, 'summary': summary, 'actors': ['Mira'], 'location': None},
            'evidence_ids': ['p1'], 'source_passage_ids': ['p1'], 'confidence': .5,
            'relevance_pass': True, 'contribution_pass': True, 'merge_processed': True,
            'reason': 'Retirement and 1 December are stated; the year is missing. Find explicit year context.'}


def state(pool=None):
    return {'dataset': 'fixture', 'topic': 'Mira', 'keywords': ['Mira'], 'timeline_events': [],
            'candidate_pool': pool or [], 'search_history': []}


def test_reasons_and_provenance_survive_exact_key_deduplication(pipeline):
    a, b = candidate(), candidate('old2')
    b['evidence_ids'] = ['p2']
    b['reason'] = 'Year remains unknown; find the year of this retirement announcement.'
    s = state([a, b])
    pipeline.refresh_evidence_memory(s)
    lead = s['exploration_memory']['lead_queue'][0]
    assert lead['evidence_ids'] == ['p1', 'p2']
    assert lead['candidate_ids'] == ['old', 'old2']
    assert lead['reasons'] == [a['reason'], b['reason']]
    view = batch_memory.policy_view(s, 'SKELETON_EXPLORATION')
    assert view['pending_leads'][0]['reason'] == b['reason']
    # Paraphrases are NOT automatically identified as the same event by a hash.
    s['candidate_pool'].append(candidate('paraphrase', 'Mira states that she will retire from athletics.'))
    pipeline.refresh_evidence_memory(s)
    assert len(s['exploration_memory']['lead_queue']) == 2


def test_rotation_exposes_all_176_leads_and_excludes_resolved(pipeline):
    s = state([candidate(str(i), f'Mira milestone number {i} was reported.') for i in range(176)])
    pipeline.refresh_evidence_memory(s)
    seen = set()
    for _ in range(22):
        view = batch_memory.policy_view(s, 'SKELETON_EXPLORATION')
        rows = view['pending_leads']
        assert len(rows) == 8
        assert not ({r['lead_id'] for r in rows} & seen)
        seen.update(r['lead_id'] for r in rows)
        record_presentation(s, rows)
        pipeline.refresh_evidence_memory(s)
    assert len(seen) == 176
    assert batch_memory.policy_view(s, 'SKELETON_EXPLORATION')['pending_lead_counts']['never_shown'] == 0
    s['exploration_memory']['lead_queue'][0]['status'] = 'RESOLVED'
    view = batch_memory.policy_view(s, 'SKELETON_EXPLORATION')
    assert view['pending_lead_counts']['open'] == 175
    assert all(r['status'] == 'OPEN' for r in view['pending_leads'])


def test_targeted_queries_reject_invented_leads_and_write_attempts(pipeline, monkeypatch):
    import batched_search_phase as runtime
    s = state([candidate()])
    pipeline.refresh_evidence_memory(s)
    lead = s['exploration_memory']['lead_queue'][0]
    q = {'query': 'Mira retirement announcement year', 'target_lead_ids': [lead['lead_id']],
         'time_filter': {'mode': 'none', 'start': None, 'end': None}}
    action = {'reason': 'Find the missing year of the retirement announcement.', 'action': 'SEARCH',
              'queries': [q], 'stop_reason': None}
    assert validate_action(action, [], visible_lead_ids=[lead['lead_id']]) == action
    with pytest.raises(ValueError, match='visible'):
        validate_action(action, [], visible_lead_ids=[])
    monkeypatch.setattr(runtime, 'search', lambda *a, **k: [])
    monkeypatch.setattr(pipeline, 'retrieve_passages', lambda *a, **k: [])
    monkeypatch.setattr(pipeline, 'process_passages', lambda *a, **k: None)
    runtime.execute_batch(None, s, {'top_k': 2}, None, {'steps': []}, {}, 'SKELETON_EXPLORATION', action, {})
    pipeline.refresh_evidence_memory(s)
    lead = s['exploration_memory']['lead_queue'][0]
    assert lead['attempted_queries'] == [q['query']]
    assert lead['status'] == 'OPEN' and lead['last_result_count'] == 0


def source_pair():
    text = 'Mira announced her retirement on 1 December.\nThe retirement announcement took place in 2020.'
    doc = {'id': 'article', 'title': 'Mira retirement', 'publication_date': '2021-02-03'}
    split = text.index('\n')
    first = _passage(doc, text, 0, split)
    second = _passage(doc, text, split + 1, len(text))
    first['context_before'] = second['context_before'] = ''
    return first, second


def date_evidence(first, second):
    return {'document_id': first['id'], 'quote': first['text'], 'time_expression': '1 December',
            'context': {'document_id': second['id'], 'quote': second['text'], 'year': '2020'}}


def test_joint_year_requires_cited_same_frozen_article_and_literal_year():
    first, second = source_pair()
    evidence = date_evidence(first, second)
    checked = validate_date_evidence('2020-12-01', evidence, [first, second], [first['id'], second['id']])
    assert checked['context']['document_id'] == second['id']
    with pytest.raises(ValueError, match='same'):
        validate_date_evidence('2020-12-01', evidence, [first, second], [first['id']])
    other = {**second, 'document_id': 'unrelated'}
    with pytest.raises(ValueError, match='same'):
        validate_date_evidence('2020-12-01', evidence, [first, other], [first['id'], second['id']])
    wrong_version = {**second, 'document_sha256': 'another-version'}
    with pytest.raises(ValueError, match='same'):
        validate_date_evidence('2020-12-01', evidence, [first, wrong_version], [first['id'], second['id']])
    evidence['context'].update(quote='2021-02-03', year='2021')
    with pytest.raises(ValueError, match='verbatim'):
        validate_date_evidence('2021-12-01', evidence, [first, second], [first['id'], second['id']])


def test_joint_reverification_enters_timeline_and_resolves_old_lead(pipeline):
    first, second = source_pair()
    old = candidate()
    old['evidence_ids'] = old['source_passage_ids'] = [first['id']]
    s = state([old])
    processed = set()
    s['_evidence_reader'] = SimpleNamespace(passages={p['id']: p for p in (first, second)},
        mark_processed=lambda batch: processed.update(p['id'] for p in batch), summary=lambda: {})
    pipeline.refresh_evidence_memory(s)
    requests = []
    class Client:
        def chat(self, messages, temperature=0):
            p = json.loads(messages[1]['content'])
            requests.append(p)
            if p['stage'] == 'VERIFY':
                assert {d['id'] for d in p['retrieved_documents']} == {first['id'], second['id']}
                pending = p['state_before_verify']['memory']['pending_candidates']
                assert pending[0]['candidate_id'] == 'old'
                assert pending[0]['reason'] == old['reason']
                assert p['state_before_verify']['memory']['already_extracted'] == []
                row = {**candidate('new', 'Mira announced retirement from her athletics career.'),
                       'status': 'SUPPORTED', 'revises_candidate_ids': ['old'],
                       'event': {'time': '2020-12-01', 'summary': 'Mira announced retirement from her athletics career.'},
                       'evidence_ids': [first['id'], second['id']], 'date_evidence': date_evidence(first, second),
                       'reason': 'The retirement statement and explicit year context jointly establish the full date.'}
                answer = {'thought': 'Combine the retirement statement with its explicit year context.',
                          'candidates': [row], 'extraction_complete': True}
            else:
                assert p['stage'].startswith('MERGE:')
                answer = {'thought': 'Append the newly grounded retirement announcement.', 'operations': [
                    {'candidate_id': p['verified_candidates'][0]['candidate_id'], 'operation': 'APPEND',
                     'reason': 'Distinct supported retirement announcement.'}]}
            return ChatResult(json.dumps(answer), 'fixture', {'total_tokens': 1}, 'offline')
    cfg = {'coverage_pipeline': {'verification_batch_passages': 1, 'candidates_per_batch': 6},
           'batch_controller': {'enabled': True}, 'label_repair_attempts': 0, 'temperature': 0,
           'strict_date_evidence': True}
    usage = dict.fromkeys(['prompt_tokens', 'completion_tokens', 'total_tokens', 'logical_calls', 'http_attempts'], 0)
    trace = {'steps': [], 'audits': []}
    pipeline.process_passages(Client(), s, [second], cfg, trace, usage, 'SKELETON_EXPLORATION',
                              {'events': [], 'memory': {}, 'valid_actions': ['MERGE']})
    assert len(s['timeline_events']) == 1
    event = s['timeline_events'][0]
    assert event['time'] == '2020-12-01'
    assert set(event['evidence_ids']) == {first['id'], second['id']}
    assert s['candidate_pool'][0]['status'] == 'INSUFFICIENT'  # Keep original audit result.
    assert s['exploration_memory']['lead_queue'][0]['status'] == 'RESOLVED'
    view = batch_memory.policy_view(s, 'SKELETON_EXPLORATION')
    assert view['pending_leads'] == []
    assert view['event_delta'][0]['event']['time'] == '2020-12-01'
    assert len(requests) == 2


def test_unresolved_reassessment_keeps_precise_reason_and_does_not_close(pipeline):
    old = candidate()
    new = {**candidate('new'), 'revises_candidate_ids': ['old'],
           'reason': 'The year is known but no day is stated. Find the day of the actual announcement.'}
    s = state([old, new])
    record_resolutions(s, [new], [])
    pipeline.refresh_evidence_memory(s)
    row = batch_memory.policy_view(s, 'SKELETON_EXPLORATION')['pending_leads'][0]
    assert row['reason'] == new['reason'] and row['status'] == 'OPEN'
    assert not s['timeline_events']


def test_missing_reason_or_unbound_reassessment_is_rejected():
    doc = {'id': 'p1', 'text': 'Mira announced her retirement.'}
    row = candidate()
    row.pop('reason')
    with pytest.raises(ValueError, match='reason'):
        _checked(row, [doc], True)
    row['reason'] = 'Retirement is stated; the date is missing. Find a dated announcement.'
    row['revises_candidate_ids'] = ['unknown']
    with pytest.raises(ValueError, match='supplied'):
        _checked(row, [doc], True, [candidate()])


def test_compaction_preserves_pending_reasons_and_person_prompt(pipeline):
    from run_full_timeline_api_agent import build_verify_instruction
    from chronos_repro.batch_policy import POLICY_SYSTEM
    visible = {'events': [], 'memory': {'already_extracted': [], 'pending_candidates': [candidate()]}}
    cfg = {'max_candidates_per_round': 6, 'coverage_extraction': True, 'coarse_extraction': True}
    raw = build_verify_instruction({'model_visible_state': visible}, [{'id': 'p1', 'text': 'Mira retired.'}], cfg)
    projected = compact_instruction(raw)
    assert projected['state_before_verify']['memory']['pending_candidates'][0]['reason'] == candidate()['reason']
    assert 'achievements and honors' in POLICY_SYSTEM and 'accidents' in POLICY_SYSTEM
    assert 'same actor, action and occurrence' in raw['objective']


def test_joint_context_excludes_unselected_unread_passages():
    first, second = source_pair()
    s = state()
    s['_evidence_reader'] = SimpleNamespace(passages={p['id']: p for p in (first, second)})
    assert expand_batch([first], [first], s, {}) == [first]
    assert expand_batch([first], [first, second], s, {}) == [first, second]


def test_irrelevant_is_archived_but_never_sent_to_merge_or_search(pipeline):
    first, _ = source_pair()
    s = state()
    s['_evidence_reader'] = SimpleNamespace(passages={first['id']: first},
        mark_processed=lambda docs: None, summary=lambda: {})
    calls = []
    class Client:
        def chat(self, messages, temperature=0):
            p = json.loads(messages[1]['content'])
            calls.append(p['stage'])
            assert p['stage'] == 'VERIFY'
            row = {**candidate('unrelated'), 'status': 'IRRELEVANT', 'relevance_pass': False,
                   'contribution_pass': False, 'evidence_ids': [first['id']],
                   'reason': 'The passage concerns another person, not the requested subject.'}
            return ChatResult(json.dumps({'thought': 'Exclude unrelated evidence from the timeline.',
                'candidates': [row], 'extraction_complete': True}), 'fixture', {}, 'offline')
    cfg = {'coverage_pipeline': {'verification_batch_passages': 1, 'candidates_per_batch': 6},
           'label_repair_attempts': 0, 'temperature': 0}
    usage = dict.fromkeys(['prompt_tokens', 'completion_tokens', 'total_tokens', 'logical_calls', 'http_attempts'], 0)
    trace = {'steps': [], 'audits': []}
    pipeline.process_passages(Client(), s, [first], cfg, trace, usage, 'SKELETON_EXPLORATION',
                              {'events': [], 'memory': {}, 'valid_actions': ['MERGE']})
    assert calls == ['VERIFY']
    assert s['candidate_pool'][0]['status'] == 'IRRELEVANT'
    assert not s['timeline_events'] and not s['exploration_memory']['lead_queue']
    assert trace['steps'][0]['observation']['passed_candidate_ids'] == []


def test_budget_reduction_keeps_full_reasons_and_rotates_after_actual_call(pipeline, monkeypatch):
    import batched_search_phase as runtime
    s = state([candidate(str(i), f'Mira milestone number {i} was reported.') for i in range(12)])
    pipeline.refresh_evidence_memory(s)
    monkeypatch.setattr(runtime, 'refresh_summary', lambda *a, **k: False)
    monkeypatch.setattr(runtime, '_fits', lambda c, system, payload: len(payload['state']['pending_leads']) <= 3)
    def call(client, s, cfg, trace, usage, stage, payload, validator, **kwargs):
        return validator({'reason': 'Investigate a distinct stage of the career.', 'action': 'SEARCH',
            'queries': [{'query': 'Mira career major achievements', 'target_lead_ids': [],
                         'time_filter': {'mode': 'none', 'start': None, 'end': None}}], 'stop_reason': None})
    monkeypatch.setattr(runtime, '_call', call)
    seen = set()
    for _ in range(4):
        _, payload = runtime.decide(None, s, {'batch_controller': {}}, {}, {}, 'SKELETON_EXPLORATION')
        rows = payload['state']['pending_leads']
        assert len(rows) == 3 and all(r['reason'] == candidate()['reason'] for r in rows)
        assert not seen & {r['lead_id'] for r in rows}
        seen.update(r['lead_id'] for r in rows)
    assert len(seen) == 12
