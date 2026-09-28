import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batched_search_phase as runtime
from chronos_repro import batch_memory as memory
from chronos_repro.gap_discovery import bind_anchors, validate_discovery, recover_discovery
from chronos_repro.llm import ChatResult


def event(i, text='An earlier milestone occurred.'):
    return {'event_id': i, 'time': '2016-05-12', 'summary': text, 'evidence_ids': ['d1']}


def proposal(anchor='event-002', quote='the Senate voted to begin an impeachment trial'):
    return {'thought': 'The pending trial outcome warrants a focused query.', 'action': 'GAP_MEMORY', 'gaps': [{
        'gap_id': 'g1', 'type': 'MISSING_KEY_EVENT', 'description': 'The pending trial outcome remains unknown.',
        'priority': .9, 'status': 'OPEN', 'left_event_id': anchor, 'right_event_id': None,
        'retrieval_target': {'question': 'What was the final outcome of the Senate impeachment trial against Dilma Rousseff?',
            'anchor_quote': quote, 'completion_criterion': 'A sourced report states the final trial verdict.',
            'seed_query': 'Dilma Rousseff Senate impeachment trial final verdict'}}]}


def fixture_state():
    events = [event(f'early-{i}') for i in range(12)] + [event('event-002',
        'Dilma Rousseff was stripped of her presidential duties after the Senate voted to begin an impeachment trial.')]
    state = {'dataset': 'entities', 'topic': 'Dilma_Rousseff', 'timeline_events': events}
    memory.sync_events(state)
    memory.commit_summary(state, [{'text': 'The Senate voted to begin a trial.', 'event_ids': ['event-002']}],
                          memory.initialize(state)['revision'])
    return state


def test_memory_anchor_is_explicitly_supplied_and_accepted():
    state = fixture_state(); events = state['timeline_events']
    anchors, facts = bind_anchors(events[:12], state['controller_memory']['facts'], events)
    assert len(anchors) == 13 and facts[0]['event_ids'] == ['event-002']
    assert validate_discovery(proposal(), anchors)['gaps'][0]['left_event_id'] == 'event-002'
    anchors[-1]['summary'] = 'Modified outside the state.'
    assert events[-1]['summary'] != anchors[-1]['summary']


def test_unknown_fact_references_do_not_create_allowed_anchors():
    anchors, facts = bind_anchors([event('a')], [{'text':'Something happened.', 'event_ids':['missing']}], [event('a')])
    assert facts == [] and [e['event_id'] for e in anchors] == ['a']


def test_repair_constraints_name_allowed_ids_and_exact_quote_source():
    with pytest.raises(ValueError) as captured:
        validate_discovery(proposal(), [event('other')])
    context = captured.value.repair_context
    assert context['allowed_anchor_ids'] == ['other']
    assert 'events[].summary' in context['instruction'] and 'never from fact_memory' in context['instruction']


def test_summary_paraphrase_is_still_rejected_after_scope_fix():
    anchors = [event('event-036', 'Senators continue speeches ahead of the impeachment vote.')]
    with pytest.raises(ValueError, match='verbatim'):
        validate_discovery(proposal('event-036','senators continued speeches ahead of the impeachment vote'), anchors)


def test_partial_recovery_keeps_good_proposal_and_quarantines_bad_one():
    state = fixture_state(); raw = proposal()
    invalid = copy.deepcopy(raw['gaps'][0]); invalid['gap_id'] = 'g2'; invalid['left_event_id'] = 'invented'
    raw['gaps'].append(invalid)
    out, audit = recover_discovery(raw, state['timeline_events'])
    assert len(out['gaps']) == 1 and out['gaps'][0]['left_event_id'] == 'event-002'
    assert len(audit['rejected']) == 1 and audit['discovery_complete'] is False
    assert audit['training_target'] is False


@pytest.mark.parametrize('raw', ['bad JSON response', {'action':'GAP_MEMORY','gaps':[None]},
    {'action':'GAP_MEMORY','gaps':[{}]*4}])
def test_malformed_recovery_is_incomplete_not_no_gap(raw):
    out, audit = recover_discovery(raw, [event('a')])
    assert not out['gaps'] and audit['discovery_complete'] is False


def test_discovery_sends_the_same_scope_to_model_and_validator(monkeypatch):
    state=fixture_state(); packets=[]
    def call(client,s,config,trace,usage,stage,payload,validator,**kwargs):
        packets.append(payload)
        assert set(payload['allowed_anchor_ids']) == {e['event_id'] for e in payload['events']}
        assert all(set(f['event_ids']) <= set(payload['allowed_anchor_ids']) for f in payload['fact_memory'])
        return validator(proposal())
    monkeypatch.setattr(runtime, '_call', call)
    result=runtime.discover_gaps(None,state,{}, {'steps':[]}, {})
    assert 'event-002' in packets[0]['allowed_anchor_ids']
    assert len(result['gaps']) == 1 and not result['discovery_incomplete_windows']


def test_budget_reduction_removes_memory_with_its_extra_anchors(monkeypatch):
    state=fixture_state(); packets=[]
    monkeypatch.setattr(runtime,'_fits',lambda client,system,payload: not payload['fact_memory'] and len(payload['events']) <= 5)
    def call(client,s,config,trace,usage,stage,payload,validator,**kwargs):
        packets.append(payload)
        return validator({'thought':'No pending question is supported by this window.','action':'GAP_MEMORY','gaps':[]})
    monkeypatch.setattr(runtime,'_call',call)
    runtime.discover_gaps(None,state,{}, {'steps':[]}, {})
    assert all(not p['fact_memory'] and len(p['events']) <= 5 for p in packets)
    assert 'event-002' not in packets[0]['allowed_anchor_ids']
    assert 'event-002' in packets[-1]['allowed_anchor_ids']


def test_exhausted_window_is_quarantined_and_not_reported_as_autonomous_completion(monkeypatch):
    class Client:
        def __init__(self): self.calls=0
        def chat(self,messages,temperature=0):
            self.calls+=1
            packet=json.loads(messages[-1]['content'])
            assert packet['stage']=='GAP_DISCOVERY'
            if self.calls==2:
                assert packet['repair']['constraints']['allowed_anchor_ids']==['only']
            return ChatResult(json.dumps(proposal('missing')), 'fixture', {}, None)
    client=Client();state={'dataset':'fixture','topic':'trial','timeline_events':[event('only')]}
    memory.sync_events(state)
    monkeypatch.setattr(runtime,'refresh_summary',lambda *a,**kw:None)
    monkeypatch.setattr(runtime.coverage_pipeline,'checkpoint',lambda *a:None)
    trace={'steps':[],'audits':[]};usage={k:0 for k in ['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts']}
    config={'phase2_max_gap_cycles':2,'label_repair_attempts':1,'temperature':0}
    runtime.run_phase2(client,state,config,None,trace,usage)
    assert client.calls==2 and state['phase2_termination']=='gap_discovery_incomplete'
    assert len(state['gap_memory']['discovery_incomplete_windows'])==1
    assert trace['steps'][0]['observation']['training_target'] is False


def test_failed_window_does_not_abort_later_windows(monkeypatch):
    state=fixture_state();calls=[]
    def call(client,s,config,trace,usage,stage,payload,validator,**kwargs):
        calls.append(payload)
        if len(calls)==1:return kwargs['recovery'](proposal('missing'))[0]
        return validator(proposal())
    monkeypatch.setattr(runtime,'_call',call)
    result=runtime.discover_gaps(None,state,{}, {'steps':[]}, {})
    assert len(calls)==2 and len(result['gaps'])==1
    assert len(result['discovery_incomplete_windows'])==1


def test_explicit_continuation_retries_incomplete_discovery_once(monkeypatch):
    class Client:
        calls=0
        def chat(self,messages,temperature=0):
            self.calls+=1
            return ChatResult(json.dumps({'thought':'No anchored uncertainty remains in this window.',
                'action':'GAP_MEMORY','gaps':[]}), 'fixture', {}, None)
    state={'dataset':'fixture','topic':'trial','timeline_events':[event('only')]}
    memory.sync_events(state)
    state['gap_memory']={'gaps':[],'discovery_revision':memory.initialize(state)['revision'],
                         'discovery_incomplete_windows':[{'window_event_ids':['only']}]}
    monkeypatch.setattr(runtime,'refresh_summary',lambda *a,**kw:None)
    monkeypatch.setattr(runtime.coverage_pipeline,'checkpoint',lambda *a:None)
    client=Client();trace={'steps':[],'audits':[]}
    usage={k:0 for k in ['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts']}
    runtime.run_phase2(client,state,{'phase2_max_gap_cycles':2,'label_repair_attempts':1,'temperature':0},None,trace,usage)
    assert client.calls==1 and state['phase2_termination']=='no_actionable_gaps'
    assert state['gap_memory']['discovery_incomplete_windows']==[]
