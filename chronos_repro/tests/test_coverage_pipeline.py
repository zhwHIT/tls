import copy
import json
import re
import sqlite3
from pathlib import Path

import pytest

from chronos_repro.evidence_access import EvidenceReader, split_passages
from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.exploration_memory import initial_memory
from chronos_repro.llm import ChatResult, DeepSeekClient, InsufficientBalanceError, RetryableLLMError
from chronos_repro.limited_llm import LimitedDeepSeekClient, RequestLimitError


def database(tmp_path, rows):
    path = tmp_path / 'docs.sqlite3'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE documents (doc_id TEXT,title TEXT,text TEXT,timestamp TEXT,topic TEXT)')
        c.executemany('INSERT INTO documents VALUES (?,?,?,?,?)', rows)
    return path


def test_passages_are_contiguous_cover_body_and_keep_middle_evidence():
    text = ('unrelated background ' * 200) + 'Critical launch occurred 2020-07-27. ' + ('ordinary later details ' * 200)
    parts = split_passages({'id': 'd', 'text': text}, 80, 700, 10)
    assert len(parts) > 3
    assert any('Critical launch occurred 2020-07-27.' in p['text'] for p in parts)
    covered = set()
    for p in parts:
        assert p['text'] == text[p['source_start']:p['source_end']]
        assert len(p['text']) <= 700
        covered.update(range(p['source_start'], p['source_end']))
    assert all(i in covered for i, ch in enumerate(text) if not ch.isspace())


def test_reader_ranks_query_passage_and_does_not_mark_unread_as_processed(tmp_path):
    text = ('old background ' * 400) + 'Aurora landing engine failure investigation 2020-07-27. ' + ('misc news ' * 400)
    path = database(tmp_path, [('d', 'Aurora', text, '2020-01-01', 'aurora')])
    reader = EvidenceReader(path, 'aurora', {'passage_words': 80, 'passage_chars': 700, 'overlap_words': 10})
    reader.add_results([{'id': 'd'}])
    selected = reader.select('landing engine failure investigation', 1)
    assert 'engine failure' in selected[0]['text']
    assert reader.summary()['processed_passage_count'] == 0
    reader.mark_processed(selected)
    assert reader.summary()['processed_passage_count'] == 1
    assert selected[0]['id'] not in {p['id'] for p in reader.select('landing engine failure investigation', 2)}
    assert reader.summary()['unread_passage_count'] > 0


def test_year_context_can_complete_year_but_not_day():
    doc = {'id': 'p1', 'title': 'Aurora chronology 2013', 'text': 'July 27: Aurora lands on target.'}
    evidence = {'document_id': 'p1', 'quote': doc['text'], 'time_expression': 'July 27',
                'context': {'document_id': 'p1', 'quote': doc['title'], 'year': '2013'}}
    checked = validate_date_evidence('2013-07-27', evidence, [doc], ['p1'])
    assert checked['normalization'] == 'contextual_year'
    evidence['time_expression'] = 'July'
    with pytest.raises(ValueError, match='precision'):
        validate_date_evidence('2013-07-01', evidence, [doc], ['p1'])
    evidence['context']['quote'] = '2011-02-03'
    with pytest.raises(ValueError, match='verbatim'):
        validate_date_evidence('2013-07-27', evidence, [doc], ['p1'])


def test_request_limit_includes_retries_and_balance_stops_immediately(monkeypatch):
    calls = []
    def retry(*args):
        calls.append(1)
        raise RetryableLLMError('temporary failure')
    monkeypatch.setattr(DeepSeekClient, '_chat_once', retry)
    client = LimitedDeepSeekClient(request_limit=2, max_retries=4, retry_backoff_seconds=0)
    with pytest.raises(RequestLimitError):
        client.chat([])
    assert len(calls) == client.http_requests_started == 2
    def balance(*args):
        raise InsufficientBalanceError('insufficient balance')
    monkeypatch.setattr(DeepSeekClient, '_chat_once', balance)
    client = LimitedDeepSeekClient(request_limit=10, max_retries=4, retry_backoff_seconds=0)
    with pytest.raises(InsufficientBalanceError):
        client.chat([])
    assert client.http_requests_started == 1


def config():
    return {'model': 'fake', 'exploration_memory': {'enabled': True},
            'coverage_pipeline': {'enabled': True, 'passage_words': 500, 'passage_chars': 3200,
                                  'overlap_words': 30, 'passages_per_search': 2,
                                  'verification_batch_passages': 1, 'candidates_per_batch': 12, 'extraction_pages': 2},
            'phase1_max_rounds': 1, 'phase1_min_search_rounds': 1, 'phase1_min_events': 4,
            'top_k': 2, 'document_char_limit': 3200, 'max_candidates_per_round': 12,
            'label_repair_attempts': 1, 'temperature': 0, 'phase2_max_gaps': 8,
            'phase2_max_gap_cycles': 1, 'phase2_teacher_guidance': False,
            'temporal_search': {'enabled': True}}


class FixtureClient:
    model = 'fake'
    def __init__(self):
        self.requests = []

    def chat(self, messages, temperature=0):
        request = json.loads(messages[1]['content'])
        self.requests.append(request)
        assert 'PRIVATE_SENTINEL' not in json.dumps(request)
        stage = request['stage']
        if 'choose diversified' in stage:
            answer = {'thought': 'Explore the broad chronology across all available stages.', 'action': 'SEARCH',
                      'strategy': 'DISCOVER', 'query': 'Aurora mission chronological events'}
        elif stage == 'VERIFY':
            candidates = []
            assert request['state_before_verify']['events'] == []
            for d in request['retrieved_documents']:
                for i, line in enumerate(d['text'].splitlines()):
                    match = re.match(r'(\d{4}-\d{2}-\d{2}): (.*)', line)
                    if match:
                        day, summary = match.groups()
                        candidates.append({'candidate_id': f'c{i}', 'status': 'SUPPORTED',
                            'event': {'time': day, 'summary': summary, 'actors': [], 'location': None},
                            'date_evidence': {'document_id': d['id'], 'quote': line, 'time_expression': day},
                            'evidence_ids': [d['id']], 'confidence': 0.9, 'relevance_pass': True, 'contribution_pass': True})
            answer = {'thought': 'The passages contain multiple directly dated mission milestones.', 'candidates': candidates, 'extraction_complete': True}
        elif stage.startswith('MERGE:'):
            answer = {'thought': 'These are distinct dated mission developments.',
                      'operations': [{'candidate_id': c['candidate_id'], 'operation': 'APPEND', 'reason': 'distinct event'} for c in request['verified_candidates']]}
        elif 'MEMORY_UPDATE' in stage:
            visible = request['student_visible_state']
            assert len(visible['events']) == 10
            assert visible['memory']['search_history'][-1]['new_verified_event_count'] == 10
            evidence = visible['memory']['search_history'][-1]['result_ids'][0]
            answer = {'thought': 'The merged milestones establish several coarse mission stages.', 'action': 'MEMORY_UPDATE',
                      'observations': [], 'discovered_keywords': [], 'next_search_directions': ['Probe later mission outcomes.'],
                      'deferred_directions': [], 'skeleton_ready': False,
                      'boundary_assessment': 'Later outcome evidence still requires a probe.',
                      'stage_outline': [{'period': str(y), 'description': 'A documented mission development stage.', 'evidence_ids': [evidence]} for y in (2019,2020,2021)]}
        elif stage == 'FINAL_SELECT':
            answer = {'thought': 'Retain each distinct supported milestone in temporal order.',
                      'keep_event_ids': [e['event_id'] for e in request['events']], 'drop': []}
        else:
            raise AssertionError(stage)
        return ChatResult(json.dumps(answer), 'fake', {'total_tokens': 1}, 'offline-test')


def test_v6_real_phase1_batches_more_than_six_events_and_refreshes_memory_after_merge(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import coverage_pipeline as pipeline
    import run_tisa_two_phase_annotation as runner
    rows = [(str(year), 'Aurora mission', '\n'.join(f'{year}-01-{i:02d}: Aurora milestone number {i} occurred successfully.' for i in range(1,6)), f'{year}-02-01', 'aurora') for year in (2019,2020)]
    path = database(tmp_path, rows)
    monkeypatch.setattr(pipeline, 'search', lambda *a, **k: [{'id': r[0]} for r in rows])
    state = {'dataset': 'fixture', 'topic': 'aurora', 'keywords': ['Aurora'], 'timeline_events': [],
             'search_history': [], 'exploration_memory': initial_memory(['Aurora']), '_output_dir': str(tmp_path)}
    trace = {'trajectory_id': 'fixture-v6', 'dataset': 'fixture', 'topic': 'aurora', 'steps': [], 'audits': []}
    usage = dict.fromkeys(['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts'], 0)
    client = FixtureClient()
    runner.run_phase1(client, state, config(), path, trace, usage)
    assert len(state['timeline_events']) == len(state['candidate_pool']) == 10
    assert [s['action'] for s in trace['steps']] == ['SEARCH','VERIFY','MERGE','VERIFY','MERGE','MEMORY_UPDATE','STOP']
    assert state['evidence_progress']['processed_passage_count'] == 2
    assert all(s['model_input']['memory']['already_extracted'] == [] for s in trace['steps'] if s['action'] == 'VERIFY')
    assert state['exploration_memory']['provisional_leads'] == []
    assert (tmp_path / 'checkpoint.json').exists()
    assert not any(r['metadata']['action'] == 'STOP' for r in runner.compile_sft_rows(trace))
    pipeline.finalize(client, state, config(), trace, usage)
    assert len(state['event_pool']) == len(state['timeline_events']) == 10
    assert not any(r['metadata']['action'] == 'SELECT' for r in runner.compile_sft_rows(trace))
    from audit_coverage_rollout import audit_trace
    originals = {r[0]: {'title': r[1], 'text': r[2]} for r in rows}
    audit = audit_trace(trace, state['_evidence_reader'].manifest(), originals)
    assert audit['valid'] and audit['verified_batches_checked'] == 2
    changed = copy.deepcopy(trace)
    next(s for s in changed['steps'] if s['action'] == 'VERIFY')['model_input']['tool_observation']['retrieved_documents'][0]['text'] = 'tampered'
    assert not audit_trace(changed, state['_evidence_reader'].manifest(), originals)['valid']


def test_v6_phase2_uses_reader_and_merges_each_batch(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_tisa_two_phase_annotation as runner
    rows = [(str(year), 'Aurora mission', '\n'.join(f'{year}-01-{i:02d}: Aurora milestone number {i} occurred successfully.' for i in range(1,6)), f'{year}-02-01', 'aurora') for year in (2019,2020)]
    path = database(tmp_path, rows)
    gap = {'gap_id': 'g1', 'type': 'TEMPORAL_GAP', 'description': 'The mission chronology needs additional milestones.',
           'priority': 0.8, 'status': 'OPEN', 'left_event_id': None, 'right_event_id': None, 'attempted_queries': []}
    state = {'dataset': 'fixture', 'topic': 'aurora', 'keywords': ['Aurora'], 'timeline_events': [], 'search_history': []}
    trace = {'trajectory_id': 'fixture-phase2', 'dataset': 'fixture', 'topic': 'aurora', 'steps': [], 'audits': []}
    usage = dict.fromkeys(['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts'], 0)
    visible = {'events': [], 'memory': {}, 'valid_actions': ['GAP_MEMORY']}
    monkeypatch.setattr(runner, 'initialize_gap_memory', lambda *args: (visible, {'student_output': {'gaps': [gap]}, 'teacher_alignment': {}}, []))
    monkeypatch.setattr(runner, 'search', lambda *args, **kwargs: [{'id': r[0]} for r in rows])
    monkeypatch.setattr(runner, 'decide_gap_search', lambda *args: ({'action': 'SEARCH', 'gap_id': 'g1', 'query': 'Aurora chronological milestones',
                         'date_filter': {'mode': 'none', 'date_from': None, 'date_to': None, 'anchor_event_ids': [], 'padding_days': 0}}, []))
    monkeypatch.setattr(runner, 'refresh_gap_memory', lambda *args: ({'resolved_gap_ids': ['g1'], 'gaps': []}, []))
    monkeypatch.setattr(runner, 'final_stop', lambda *args: ({'action': 'STOP', 'thought': 'The selected gap was resolved by the new milestones.'}, []))
    runner.run_phase2(FixtureClient(), state, [{'event_id': 'PRIVATE_SENTINEL'}], config(), path, trace, usage)
    assert len(state['timeline_events']) == 10
    assert len([s for s in trace['steps'] if s['action'] == 'VERIFY']) == 2
    assert len([s for s in trace['steps'] if s['action'] == 'MERGE']) == 2
    assert all('@' in c['evidence_ids'][0] for c in state['candidate_pool'])


def test_long_unbroken_text_is_not_lost():
    text = '字' * 900
    parts = split_passages({'id': 'zh', 'text': text}, 50, 200, 5)
    assert ''.join(p['text'] for p in parts) == text


def test_distinct_same_day_events_are_not_forced_into_update():
    from chronos_repro.full_timeline import validate_merge_operations
    existing = {'event_id': 'e1', 'time': '2020-01-01', 'summary': 'Aurora mission crew approved a launch proposal'}
    candidate = {'candidate_id': 'c1', 'status': 'SUPPORTED',
                 'event': {'time': '2020-01-01', 'summary': 'Aurora mission crew rejected a launch proposal'}}
    operation = {'candidate_id': 'c1', 'operation': 'APPEND'}
    assert validate_merge_operations([operation], [candidate], [existing], exact_duplicate_guard=True)
    candidate['event']['summary'] = existing['summary']
    with pytest.raises(ValueError, match='duplicates'):
        validate_merge_operations([operation], [candidate], [existing], exact_duplicate_guard=True)


def test_full_extraction_page_is_not_silently_marked_complete(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import coverage_pipeline as pipeline
    import run_tisa_two_phase_annotation as runner
    path = database(tmp_path, [('d', 'Aurora', '2020-01-01: Aurora mission began successfully.', '2020-01-01', 'aurora')])
    cfg = config()
    cfg['coverage_pipeline'].update(candidates_per_batch=1, extraction_pages=1)
    state = {'dataset': 'fixture', 'topic': 'aurora', 'keywords': ['Aurora'], 'timeline_events': []}
    reader = pipeline.reader_for(state, path, cfg)
    reader.add_results([{'id': 'd'}])
    docs = reader.select('Aurora mission')
    calls = []
    def verify(client, current, batch, config):
        seen = current['model_visible_state']['memory']['already_extracted']
        calls.append(copy.deepcopy(seen))
        rows = [] if seen else [{'candidate_id': 'c1', 'status': 'INSUFFICIENT',
                                'event': {'time': None, 'summary': 'Aurora mission began successfully.'},
                                'evidence_ids': [batch[0]['id']], 'confidence': 0.5,
                                'relevance_pass': True, 'contribution_pass': True}]
        return {'thought': 'Keep the unresolved date as a provisional lead.', 'candidates': rows, 'extraction_complete': not rows}, []
    monkeypatch.setattr(runner, 'verify_batch', verify)
    trace = {'steps': [], 'audits': []}
    usage = dict.fromkeys(['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts'], 0)
    visible = {'events': [], 'memory': {}, 'valid_actions': ['MERGE']}
    pipeline.process_passages(None, state, docs, cfg, trace, usage, 'SKELETON_EXPLORATION', visible)
    assert reader.summary()['processed_passage_count'] == 0
    assert state['incomplete_extraction']
    pipeline.process_passages(None, state, docs, cfg, trace, usage, 'SKELETON_EXPLORATION', visible)
    assert calls[1]
    assert reader.summary()['processed_passage_count'] == 1
    assert state['incomplete_extraction'] == []
