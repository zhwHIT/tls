import json
from pathlib import Path
import sys

import pytest

from chronos_repro.preextracted_events import (
    ExtractedEventStore, candidates_from_documents, apply_decisions,
    validate_decisions, verify_instruction, recover_decisions,
)


def article(source='a'):
    return {'dataset': 'entities', 'topic': 'Person', 'snapshot_id': 'snap',
            'source_id': source, 'status': 'done', 'parse_status': 'extracted',
            'date': '2020-01-01', 'job_id': 'job', 'text': 'RAW_ARTICLE_SENTINEL',
            'title': 'RAW_TITLE_SENTINEL', 'correction': {'evidence': 'RAW_EVIDENCE_SENTINEL'},
            'events': [{'event_id': 'extracted', 'event_date': '1998', 'date_precision': 'year',
                        'summary': 'Person joined the national team.', 'origin': 'llm_generated',
                        'source_evidence': 'RAW_EVIDENCE_SENTINEL'}]}


def test_article_ranking_projection_never_returns_raw_text(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps(article()) + '\n', encoding='utf-8')
    store = ExtractedEventStore(path, 'entities', 'Person', 'snap')
    calls = []
    def search(*args, **kwargs):
        calls.append((args, kwargs))
        return [{'id': 'a', 'text': 'RAW_SEARCH_SENTINEL', 'snippet': 'RAW_SNIPPET_SENTINEL'}]
    monkeypatch.setattr('chronos_repro.preextracted_events.search', search)
    docs = store.retrieve('original-index', 'person national debut', 12, date_filter_mode='none')
    assert calls[0][0] == ('original-index', ['person national debut'], 12, 'entities Person')
    assert 'RAW_' not in json.dumps(docs)
    assert docs[0]['events'][0]['event_date'] == '1998'
    with pytest.raises(ValueError, match='lacks extracted'):
        store.project([{'id': 'missing'}])


def test_duplicate_article_mappings_share_candidate_but_keep_provenance():
    docs = [{'document_id': i, 'job_id': 'job', 'events': article()['events']} for i in ('a','b')]
    candidates = candidates_from_documents(docs)
    assert len(candidates) == 1 and candidates[0]['evidence_ids'] == ['a', 'b']


def test_reused_article_ids_are_matched_by_publication_date(tmp_path):
    first, second = article(), article()
    second.update(date='2021-01-01', job_id='second-job')
    second['events'][0].update(event_id='second', summary='Person retired.')
    path = tmp_path / 'events.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in [first, second]), encoding='utf-8')
    store = ExtractedEventStore(path, 'entities', 'Person')
    store.validate_index_inventory([('a', '2020-01-01'), ('a', '2021-01-01')])
    docs = store.project([{'id': 'a', 'timestamp': '2021-01-01'}])
    assert docs[0]['document_id'] == 'a@2021-01-01'
    assert docs[0]['events'][0]['summary'] == 'Person retired.'
    with pytest.raises(ValueError, match='matching publication date'):
        store.project([{'id': 'a'}])
    with pytest.raises(ValueError, match='identities differ'):
        store.validate_index_inventory([('a', '2021-01-01')])


def test_same_date_source_alias_requires_identical_extraction(tmp_path):
    path = tmp_path / 'events.jsonl'
    first, second = article(), article()
    second.update(source_line=2, source_text_sha256='other-text')
    path.write_text('\n'.join(json.dumps(r) for r in [first, second]), encoding='utf-8')
    store = ExtractedEventStore(path, 'entities', 'Person')
    assert store.source_record_count == 2 and len(store.articles) == 1
    assert store.identical_output_aliases['a@2020-01-01'][0]['source_line'] == 2
    store.validate_index_inventory([('a', '2020-01-01'), ('a', '2020-01-01')])
    second['events'][0]['summary'] = 'Different extracted event.'
    path.write_text('\n'.join(json.dumps(r) for r in [first, second]), encoding='utf-8')
    with pytest.raises(ValueError, match='different extracted outputs'):
        ExtractedEventStore(path, 'entities', 'Person')


def test_verify_cannot_rewrite_dates_or_merge_different_dates():
    candidate = candidates_from_documents([{'document_id': 'a', 'job_id': 'job', 'events': article()['events']}])[0]
    existing = [{'event_id': 'old', 'time': '1999', 'summary': 'Person joined the national team.'}]
    decision = {'candidate_id': 'extracted', 'relevant': True, 'operation': 'MERGE',
                'target_event_id': 'old', 'reason': 'Same event.'}
    with pytest.raises(ValueError, match='different'):
        validate_decisions({'decisions': [decision]}, [candidate], existing)
    decision.update(operation='APPEND', target_event_id=None, summary='rewritten')
    with pytest.raises(ValueError, match='unexpected fields'):
        validate_decisions({'decisions': [decision]}, [candidate], existing)


def test_partial_and_unknown_events_are_accepted_without_date_invention():
    state = {'timeline_events': []}
    candidates = candidates_from_documents([{'document_id': 'a', 'job_id': 'job', 'events': article()['events']}])
    unknown = {**candidates[0], 'candidate_id': 'unknown', 'time': None, 'date_precision': 'unknown'}
    candidates.append(unknown)
    candidates.append({**candidates[0], 'candidate_id': 'month', 'time': '1998-07', 'date_precision': 'month'})
    candidates.append({**candidates[0], 'candidate_id': 'interval', 'time': None,
                       'date_precision': 'range', 'time_range': {'start': '1998-07-01', 'end': '1998-07-03'}})
    decisions = [{'candidate_id': c['candidate_id'], 'relevant': True, 'operation': 'APPEND',
                  'target_event_id': None, 'reason': 'Relevant career milestone.'} for c in candidates]
    validate_decisions({'decisions': decisions}, candidates, [])
    apply_decisions(state, candidates, decisions)
    assert {e['time'] for e in state['timeline_events']} == {'1998', '1998-07', None}
    assert all(not e['fact_check_performed'] for e in state['timeline_events'])
    assert len(state['timeline_events']) == 4
    assert not state.get('exploration_memory', {}).get('lead_queue')
    assert next(e for e in state['timeline_events'] if e['date_precision'] == 'range')['time_range'] == {
        'start': '1998-07-01', 'end': '1998-07-03'}


def test_irrelevant_items_retained_in_pool_but_not_timeline():
    state = {'timeline_events': []}
    candidates = candidates_from_documents([{'document_id': 'a', 'job_id': 'job', 'events': article()['events']}])
    apply_decisions(state, candidates, [{'candidate_id': 'extracted', 'relevant': False,
        'operation': 'IGNORE', 'target_event_id': None, 'reason': 'Wrong person.'}])
    assert state['timeline_events'] == []
    assert state['candidate_pool'][0]['status'] == 'IRRELEVANT'


def test_forward_same_batch_merge_alias_is_resolved_without_new_summary():
    first = candidates_from_documents([{'document_id': 'a', 'job_id': 'job', 'events': article()['events']}])[0]
    second = {**first, 'candidate_id': 'second', 'summary': 'Person became a national team member.', 'evidence_ids': ['b']}
    decisions = [
        {'candidate_id': 'extracted', 'relevant': True, 'operation': 'MERGE', 'target_event_id': 'second', 'reason': 'Same occurrence.'},
        {'candidate_id': 'second', 'relevant': True, 'operation': 'APPEND', 'target_event_id': None, 'reason': 'Relevant event.'}]
    validate_decisions({'decisions': decisions}, [first, second], [])
    state = {'timeline_events': []}
    apply_decisions(state, [first, second], decisions)
    assert len(state['timeline_events']) == 1
    assert state['timeline_events'][0]['summary'] == second['summary']
    assert state['timeline_events'][0]['evidence_ids'] == ['a', 'b']
    decisions[1].update(operation='MERGE', target_event_id='extracted')
    with pytest.raises(ValueError, match='Cyclic'):
        validate_decisions({'decisions': decisions}, [first, second], [])


def test_first_phase_reuses_policy_and_returns_only_events(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_preextracted_phase1 as runner
    from chronos_repro.llm import ChatResult
    policy_calls = []
    class Client:
        def chat(self, messages, temperature=0):
            payload = json.loads(messages[-1]['content'])
            stage = payload['stage']
            if stage == 'BATCH_POLICY':
                wire = json.dumps(messages)
                for forbidden in ('pending_leads', 'pending_lead_counts', 'target_lead_ids',
                                  'LEGACY_COMPLETION_SENTINEL', 'Read each pending lead reason',
                                  'batch_feedback', 'batch_event_change_count', 'gain_attribution',
                                  'consecutive_zero_change_batches', 'event_changes', 'zero-change'):
                    assert forbidden not in wire
                policy_calls.append(payload)
                if len(policy_calls) == 1:
                    response = {'action': 'SEARCH', 'reason': 'Find early career milestones.',
                        'queries': [{'query': 'Person national team debut', 'target_lead_ids': [],
                                     'time_filter': {'mode': 'none', 'start': None, 'end': None}}], 'stop_reason': None}
                else:
                    response = {'action': 'STOP', 'reason': 'No further actionable search remains.',
                                'queries': [], 'stop_reason': 'LOW_EXPECTED_GAIN'}
            elif stage == 'VERIFY':
                response = {'decisions': [{'candidate_id': c['candidate_id'], 'relevant': True,
                    'operation': 'APPEND', 'target_event_id': None, 'reason': 'Relevant career milestone.'}
                    for c in payload['candidates']]}
            elif stage == 'FACT_MEMORY':
                response = {'action': 'MEMORY_UPDATE', 'facts': [{'text': '1998: Person joined the national team.',
                              'event_ids': [payload['event_changes'][0]['event_id']]}]}
            else:
                raise AssertionError(stage)
            return ChatResult(json.dumps(response), 'fake', {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}, None)
    class Store:
        def retrieve(self, *args, **kwargs):
            return [{'document_id': 'a', 'job_id': 'job', 'events': [
                {k: v for k, v in event.items() if k != 'source_evidence'} for event in article()['events']]}]
    config = {'phase1_max_rounds': 3, 'top_k': 12, 'temperature': 0, 'label_repair_attempts': 0,
        'temporal_search': {}, 'preextracted_events': {},
        'batch_controller': {'max_queries': 3, 'summary_interval_batches': 3, 'summary_delta_chars': 4500}}
    state = {'dataset': 'entities', 'topic': 'Person', 'timeline_events': [], 'search_history': [],
             'query_batches': [], '_preextracted_events': True, 'exploration_memory': {'lead_queue': [
                 {'lead_id': 'old', 'time': '1998', 'summary': 'LEGACY_COMPLETION_SENTINEL',
                  'status': 'OPEN', 'reason': 'LEGACY_COMPLETION_SENTINEL', 'attempted_queries': []}]}}
    trajectory = {'steps': [], 'audits': []}
    usage = {k: 0 for k in ('prompt_tokens','completion_tokens','total_tokens','http_attempts','logical_calls')}
    runner.rollout(Client(), state, config, Store(), 'index', trajectory, usage, tmp_path)
    assert state['phase1_termination'] == 'autonomous_stop'
    assert state['timeline_events'][0]['time'] == '1998'
    assert {s['phase'] for s in trajectory['steps']} == {'SKELETON_EXPLORATION'}
    assert all(s['action'] not in ('GAP_MEMORY', 'FINAL_SELECT') for s in trajectory['steps'])
    assert 'RAW_' not in json.dumps(trajectory)
    assert policy_calls[1]['state']['retrieval_contract']['extraction_errors_accepted']
    assert len(state['exploration_memory']['lead_queue']) == 1  # Historical audit is neither used nor rewritten.
    assert state['exploration_memory']['lead_queue'][0]['attempted_queries'] == []
    assert 'presentation_count' not in state['exploration_memory']['lead_queue'][0]
    assert all('target_lead_ids' not in row for row in state['search_history'])
    # Partial-date event remains available to guide searches for subsequent developments.
    assert any(r['event']['time'] == '1998' for r in policy_calls[1]['state']['event_delta'])
    assert state['query_batches'][0]['event_change_count'] == 1  # Internal audit only.
    assert state['retrieval_feedback']['event_changes'] == 1


def test_gold_coverage_does_not_promote_partial_dates(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_preextracted_phase1 as runner
    gold = tmp_path / 'snapshot/Person/timelines.jsonl'
    gold.parent.mkdir(parents=True)
    gold.write_text(json.dumps([['1998-01-01', ['A']], ['1998-07-03', ['B']]]) + '\n')
    state = {'timeline_events': [{'time': '1998', 'date_precision': 'year'},
                                {'time': None, 'date_precision': 'unknown'},
                                {'time': '1998-07-03', 'date_precision': 'day'}],
             'retrieved_document_ids': ['a']}
    class Store:
        articles = {'a': {'events': [{'event_date': '1998'}, {'event_date': '1998-07-03'}]}}
    result = runner.evaluate_dates_only({'data': 'snapshot', 'topic': 'Person'}, tmp_path, state, Store())
    assert result['gold_date_recall'] == 0.5
    assert result['matched_gold_dates'] == ['1998-07-03']
    assert not result['gold_start_exactly_matched']


def test_event_mode_recovers_duplicate_policy_without_oversized_repair(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import batched_search_phase as controller
    from chronos_repro.batch_policy import validate_action, recover_duplicate_queries
    from chronos_repro.llm import ChatResult
    query = {'query': 'Person early career debut', 'time_filter': {'mode': 'none', 'start': None, 'end': None}, 'target_lead_ids': []}
    fresh = {**query, 'query': 'Person later career retirement'}
    payload = {'action': 'SEARCH', 'reason': 'Check remaining career events.', 'queries': [query, fresh], 'stop_reason': None}
    calls = []
    class Client:
        def chat(self, messages, temperature):
            calls.append(messages)
            assert len(calls) == 1, 'Duplicate policy should not issue a repair request'
            return ChatResult(json.dumps(payload), 'fake', {}, None)
    state = {'_preextracted_events': True}
    trace = {'steps': [], 'audits': []}
    usage = {k: 0 for k in ('prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts')}
    result = controller._call(Client(), state, {'label_repair_attempts': 1, 'temperature': 0}, trace, usage,
        'BATCH_POLICY', {}, lambda p: validate_action(p, [query]), policy=True,
        recovery=lambda p: recover_duplicate_queries(p, [query], maximum_queries=3, visible_lead_ids=set()))
    assert [r['query'] for r in result['queries']] == [fresh['query']]
    assert len(calls) == 1


def recovery_candidate(cid, time='2004-06-26'):
    return {'candidate_id': cid, 'time': time, 'time_range': None,
            'date_precision': 'day' if time else 'unknown', 'summary': 'Person published a memoir.',
            'evidence_ids': [cid], 'job_id': 'job'}


def recovery_decision(cid, operation='APPEND', target=None):
    return {'candidate_id': cid, 'relevant': True, 'operation': operation,
            'target_event_id': target, 'reason': 'Relevant publication milestone.'}


@pytest.mark.parametrize('target_time', [None, '2004', '2004-06-22'])
def test_invalid_date_merge_keeps_both_original_dates(target_time):
    old = recovery_candidate('old', target_time)
    state = {'timeline_events': []}
    apply_decisions(state, [old], [recovery_decision('old')])
    candidate = recovery_candidate('new')
    existing = state['timeline_events']
    row = recovery_decision('new', 'MERGE', existing[0]['event_id'])
    with pytest.raises(ValueError) as exc:
        validate_decisions({'decisions': [row]}, [candidate], existing)
    assert exc.value.repair_context['candidate_id'] == 'new'
    assert exc.value.repair_context['target_time'] == target_time
    result, audit = recover_decisions({'decisions': [row]}, [candidate], existing)
    apply_decisions(state, [candidate], result['decisions'])
    assert {e['time'] for e in state['timeline_events']} == {target_time, '2004-06-26'}
    assert audit['recoveries'][0]['original_decision'] == row
    assert not audit['deferred']


def test_missing_merge_target_does_not_guess_link_from_reason():
    candidates = [recovery_candidate('a'), recovery_candidate('b')]
    candidates[1]['summary'] = 'Person released a book.'
    rows = [recovery_decision('a', 'MERGE'), recovery_decision('b')]
    rows[0]['reason'] = 'Duplicate of candidate b.'
    result, audit = recover_decisions({'decisions': rows}, candidates, [])
    assert result['decisions'][0]['operation'] == 'APPEND'
    assert result['decisions'][0]['target_event_id'] is None
    state = {'timeline_events': []}
    apply_decisions(state, candidates, result['decisions'])
    assert len(state['timeline_events']) == 2
    assert audit['recoveries'][0]['kind'] == 'invalid_merge_preserved_as_append'


def test_recovery_isolates_bad_classification_and_breaks_cycles():
    candidates = [recovery_candidate(i) for i in ('a', 'b', 'bad', 'ignored', 'missing')]
    rows = [recovery_decision('a', 'MERGE', 'b'), recovery_decision('b', 'MERGE', 'a'),
            {**recovery_decision('bad'), 'relevant': 'yes'},
            {**recovery_decision('ignored', 'IGNORE'), 'relevant': False}]
    result, audit = recover_decisions({'decisions': rows}, candidates, [])
    assert {r['candidate_id'] for r in audit['deferred']} == {'bad', 'missing'}
    assert len(result['decisions']) == 3
    assert any(r['reason'].startswith('Break cyclic') for r in audit['recoveries'])
    state = {'timeline_events': []}
    apply_decisions(state, candidates, result['decisions'])
    assert len(state['timeline_events']) == 1
    assert next(r for r in state['candidate_pool'] if r['candidate_id'] == 'ignored')['status'] == 'IRRELEVANT'


def test_verify_failure_does_not_abort_batch_and_deferred_can_be_retried(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_preextracted_phase1 as runner
    from chronos_repro.llm import ChatResult
    candidates = [recovery_candidate('good'), recovery_candidate('bad')]
    candidates[1]['summary'] = 'Person won a prize.'
    class Client:
        repaired = False
        def chat(self, messages, temperature=0):
            payload = json.loads(messages[-1]['content'])
            rows = [recovery_decision(c['candidate_id']) for c in payload['candidates']]
            if not self.repaired:
                next(r for r in rows if r['candidate_id'] == 'bad')['relevant'] = 'yes'
            return ChatResult(json.dumps({'decisions': rows}), 'fake', {}, None)
    client = Client()
    state = {'topic': 'Person', 'timeline_events': [], 'query_batches': [], 'search_history': []}
    config = {'preextracted_events': {}, 'label_repair_attempts': 0, 'temperature': 0}
    trace = {'steps': [], 'audits': []}
    usage = {k: 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'logical_calls', 'http_attempts')}
    def run_batch(number):
        state['pending_batch'] = {'batch_id': number, 'queries': [], 'documents': [],
                                  'candidates': candidates, 'before_revision': 0}
        runner.finish_batch(client, state, config, trace, usage, tmp_path)
    run_batch(1)
    assert state['reviewed_candidate_ids'] == ['good']
    assert state['deferred_verification']['bad']['status'] == 'VERIFY_DEFERRED'
    assert state['retrieval_feedback']['deferred_verification_count'] == 1
    assert len(state['timeline_events']) == 1
    assert 'pending_batch' not in state
    saved = json.loads((tmp_path / 'checkpoint.json').read_text())
    assert saved['state']['deferred_verification']['bad']['summary'] == candidates[1]['summary']
    client.repaired = True
    run_batch(2)
    assert state['deferred_verification'] == {}
    assert len(state['timeline_events']) == 2


def test_search_budget_includes_protocol_before_paging(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import batched_search_phase as controller
    from chronos_repro import batch_memory
    from chronos_repro.llm import ChatResult
    sent = []
    class Client:
        settings = {'preflight_limit': 3800}
        def estimate(self, messages):
            payload = json.loads(messages[-1]['content'])
            recent = len(payload['state']['recent_queries'])
            return 3794 + (8 if 'protocol' in payload else 0) - (6 - recent) * 20
        def chat(self, messages, temperature=0):
            assert self.estimate(messages) <= 3800
            sent.append(json.loads(messages[-1]['content']))
            return ChatResult(json.dumps({'action': 'STOP', 'queries': [],
                'stop_reason': 'LOW_EXPECTED_GAIN', 'reason': 'No worthwhile query remains.'}), 'fake', {}, None)
    state = {'topic': 'Person', 'timeline_events': [], '_preextracted_events': True}
    for i in range(6):
        batch_memory.record_query(state, {'query': f'Person milestone {i}', 'result_ids': []})
    monkeypatch.setattr(controller.coverage_pipeline, 'checkpoint', lambda *a: None)
    trace = {'steps': [], 'audits': []}
    usage = {k: 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'logical_calls', 'http_attempts')}
    controller.decide(Client(), state, {'batch_controller': {}, 'label_repair_attempts': 0, 'temperature': 0},
                      trace, usage, 'SKELETON_EXPLORATION')
    assert len(sent) == 1
    assert sent[0]['protocol'] == 'batch-v9'
    assert len(sent[0]['state']['recent_queries']) == 3
