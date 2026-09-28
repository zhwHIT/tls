import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batched_search_phase as runtime
from run_tisa_two_phase_annotation import model_step
from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.frozen_timex import article_annotations, attach_annotations
from chronos_repro.temporal_context import annotation_context_conflict
from chronos_repro.gap_contract import gap_signature
from chronos_repro.gap_evidence import deduplicate_open_gaps, review_context


def test_step_snapshot_cannot_acquire_future_queries_or_mutated_outputs():
    trace = {'steps': []}
    gap = {'status': 'OPEN', 'attempted_queries': []}
    request = {'gap': gap}; result = {'facts': [{'text': 'Original fact'}]}; observation = {'applied': []}
    model_step(trace, 'GAP_REFINEMENT', 'GAP_MEMORY', request, result, observation, 'fixture')
    before = json.dumps(trace['steps'][0], sort_keys=True)
    gap.update(status='DEFERRED'); gap['attempted_queries'].append('later query')
    result['facts'][0]['text'] = 'Changed fact'; observation['applied'].append('later change')
    model_step(trace, 'GAP_REFINEMENT', 'GAP_MEMORY', request, result, observation, 'fixture')
    assert json.dumps(trace['steps'][0], sort_keys=True) == before
    assert trace['steps'][1]['model_input']['gap']['attempted_queries'] == ['later query']


def test_legacy_training_export_stays_diagnostic_even_if_input_metadata_claimed_ready():
    from filter_rollout_training_rows import curate
    trace = {'steps': [{'step_id': 's1', 'action': 'SEARCH', 'observation': {}}]}
    row = {'step_id': 's1', 'metadata': {'training_ready': True}, 'messages': []}
    kept, _ = curate([row], trace)
    assert kept[0]['metadata']['training_ready'] is False
    assert kept[0]['metadata']['trajectory_input_integrity'] == 'legacy_snapshot_integrity_unverified_diagnostic_only'
    assert row['metadata']['training_ready'] is True  # Preserve the original artifact.


@pytest.mark.parametrize('text,expression,target,pub,conflict', [
    ('The vote is set for Saturday and Sunday.', 'Saturday', '2012-06-09', '2012-06-15', True),
    ('The vote took place Saturday.', 'Saturday', '2012-06-09', '2012-06-15', False),
    ('Demonstrators gathered Thursday, June 21.', 'Thursday', '2012-06-14', '2012-06-15', True),
    ('Demonstrators gathered Thursday, June 21.', 'Thursday', '2012-06-21', '2012-06-22', False),
    ('On June 21, Thursday, the vote ended.', 'Thursday', '2012-06-14', '2012-06-15', True),
    ('The vote ended Thursday. A protest followed June 21.', 'Thursday', '2012-06-14', '2012-06-15', False),
    ('The vote will end Friday. It began Saturday.', 'Saturday', '2012-06-09', '2012-06-15', False),
])
def test_local_date_context_conflicts_do_not_invent_replacement_dates(text, expression, target, pub, conflict):
    start = text.index(expression)
    assert bool(annotation_context_conflict(text, start, start + len(expression), target, pub)) is conflict


def weekday_fixture():
    text = 'The vote is set for Saturday and Sunday.'
    digest = hashlib.sha256(text.encode()).hexdigest(); start = text.index('Saturday'); end = start + 8
    identity = hashlib.sha256(f'{digest}:{start}:{end}:2012-06-09'.encode()).hexdigest()[:16]
    annotation = {'annotation_id': identity, 'expression': 'Saturday', 'date': '2012-06-09',
        'source_start': start, 'source_end': end, 'rule': 'anchored_weekday',
        'publication_anchor': '2012-06-15', 'document_sha256': digest}
    doc = {'id': 'd1', 'document_id': 'd1', 'title': 'Vote', 'text': text, 'context_before': '',
        'publication_date': '2012-06-15', 'source_start': 0, 'source_end': len(text),
        'document_sha256': digest, 'temporal_annotations': [annotation]}
    return doc, annotation


def test_conflicting_legacy_annotations_are_filtered_and_cannot_bypass_validation():
    doc, annotation = weekday_fixture()
    evidence = {'document_id': 'd1', 'quote': doc['text'], 'time_expression': 'Saturday',
                'annotation_id': annotation['annotation_id']}
    with pytest.raises(ValueError, match='context conflict'):
        validate_date_evidence('2012-06-09', evidence, [doc], ['d1'])
    attach_annotations(doc, [annotation])
    assert doc['temporal_annotations'] == []
    assert doc['temporal_annotation_conflicts'][0]['reason'] == 'past_weekday_annotation_in_future_context'


def test_article_annotation_generation_rejects_future_past_weekday():
    doc, _ = weekday_fixture()
    article = {'text': doc['text'], 'time': doc['publication_date'], 'sentences': [{
        'raw': doc['text'], 'tokens': {'keys': ['raw','time','time_format'], 'data': [
            [w, '2012-06-09' if w == 'Saturday' else None, '%Y-%m-%d' if w == 'Saturday' else None]
            for w in doc['text'].split()]}}]}
    assert list(article_annotations(article)) == []


def gap(gid, question, anchor='a', status='OPEN'):
    return {'gap_id': gid, 'type': 'MISSING_KEY_EVENT', 'left_event_id': anchor, 'right_event_id': None,
        'status': status, 'attempted_queries': [], 'retrieval_target': {
            'question': question, 'completion_criterion': 'A report answers the question.'}}


def test_exact_dated_question_dedup_ignores_anchors_without_losing_history():
    a = gap('g1', "What was Egypt's December 2012 referendum result?")
    a.update(status='DEFERRED', attempted_queries=['first', 'second', 'third'], search_batches=1)
    b = gap('g2', "What was Egypt's December 2012 referendum result?", 'b')
    state = {'gaps': [a,b]}; deduplicate_open_gaps(state); deduplicate_open_gaps(state)
    assert b['status'] == 'DEFERRED' and b['duplicate_of'] == 'g1'
    assert len(state['duplicate_gap_links']) == 1
    assert a['attempted_queries'] == ['first', 'second', 'third'] and a['search_batches'] == 1
    assert len(state['gaps']) == 2 and not state['duplicate_gap_links'][0]['resolution_established']


@pytest.mark.parametrize('question', ['Who won in 2012?', 'Was its approval granted in 2012?', 'Was the agreement approved?'])
def test_context_dependent_questions_at_different_anchors_are_not_collapsed(question):
    assert gap_signature(gap('g1',question,'a')) != gap_signature(gap('g2',question,'b'))


def test_different_dates_and_questions_stay_distinct():
    assert gap_signature(gap('g1',"What was Egypt's 2012 referendum result?")) != gap_signature(
        gap('g2',"What was Egypt's 2014 referendum result?"))


def evidence_fixture():
    events = [{'event_id': f'e{i}', 'time': '2012-12-20', 'summary':
               'Egypt official referendum constitution result was announced.', 'evidence_ids': ['d']} for i in range(16)]
    events += [{'event_id': 'answer', 'time': '2012-12-26',
                'summary': "Egypt's constitution was signed into law.", 'evidence_ids': ['proof']}]
    previous = gap('old', "Did Egypt's constitution take effect after the 2012 referendum?", status='RESOLVED')
    previous['resolution_evidence'] = [{'gap_id':'old','event_id':'answer','quote':"Egypt's constitution was signed into law."}]
    active = gap('new', "Were Egypt's official referendum results announced and the constitution enacted in 2012?", 'e0')
    return events, previous, active


def test_related_resolution_is_visible_without_auto_resolving_new_question():
    events, previous, active = evidence_fixture()
    before = copy.deepcopy((events,previous,active))
    visible, proofs = review_context(events,active,[previous,active])
    assert len(visible) == 12 and {e['event_id'] for e in visible} >= {'answer','e0'}
    assert proofs[0]['event_id'] == 'answer'
    assert (events,previous,active) == before and active['status'] == 'OPEN'


@pytest.mark.parametrize('kind', ['deleted','changed','conflicted','uncited'])
def test_stale_or_invalid_prior_proof_is_not_reused(kind):
    events, previous, active = evidence_fixture()
    if kind == 'deleted': events.pop()
    elif kind == 'changed': events[-1]['summary'] = 'The plan was withdrawn.'
    elif kind == 'conflicted': events[-1]['conflict'] = True
    else: events[-1]['evidence_ids'] = []
    _, proofs = review_context(events,active,[previous,active])
    assert proofs == []


def test_review_gap_reuses_proof_and_records_input_before_state_update(monkeypatch):
    from chronos_repro.llm import ChatResult
    from chronos_repro import batch_memory as memory
    events, previous, active = evidence_fixture()
    class Client:
        def chat(self,messages,temperature=0):
            packet=json.loads(messages[-1]['content'])
            assert 'answer' in {e['event_id'] for e in packet['events']}
            return ChatResult(json.dumps({'action':'GAP_MEMORY','status':'DEFERRED',
                'reason':'Additional evidence remains needed for the full question.',
                'resolved_gap_ids':[],'resolution_evidence':[]}), 'fixture', {}, None)
    state={'dataset':'fixture','topic':'Egypt','timeline_events':events,'gap_memory':{'gaps':[previous,active]}}
    memory.sync_events(state)
    trace={'steps':[],'audits':[]};usage={k:0 for k in ['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts']}
    runtime.review_gap(Client(),state,{'label_repair_attempts':1,'temperature':0},trace,usage,active)
    assert active['status']=='DEFERRED'
    assert trace['steps'][0]['model_input']['gap']['status']=='OPEN'
    assert trace['gap_evidence_reuse'][0]['visible_proofs'][0]['event_id']=='answer'
