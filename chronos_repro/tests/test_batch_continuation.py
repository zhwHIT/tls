import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batch_continuation as continuation
import batched_search_phase as runtime
import coverage_pipeline
from chronos_repro import batch_memory as memory
from chronos_repro.atomic_io import atomic_write_json
from chronos_repro.snapshot import sha256
from chronos_repro.tisa_rollout import validate_thought, SKELETON, REFINE
from chronos_repro.llm import ChatResult


def test_goldsmith_is_a_person_not_private_reference():
    assert validate_thought("Goldsmith's legal advice is a distinct event.")
    for value in ('Use the gold label for this event.', 'Copy GOLD_reference events.', 'Use the ground truth.'):
        with pytest.raises(ValueError, match='supervision'):
            validate_thought(value)


def test_streaming_checkpoint_preserves_previous_on_serialization_failure(tmp_path):
    p = tmp_path / 'checkpoint.json'
    atomic_write_json(p, {'good': True})
    with pytest.raises(TypeError):
        atomic_write_json(p, {'bad': object()})
    assert json.loads(p.read_text()) == {'good': True}
    assert not p.with_suffix('.json.tmp').exists()


def test_continuation_seeds_total_and_keeps_parent_immutable(tmp_path):
    parent, destination = tmp_path / 'old', tmp_path / 'new'
    parent.mkdir(); destination.mkdir()
    config = {'dataset':'fixture','topic':'topic','data':'data','index':'index','model':'model',
              'phase2_teacher_guidance':False,'max_api_requests':500}
    atomic_write_json(parent/'run_config.json',config)
    atomic_write_json(parent/'run_binding.json', {'snapshot_id':'frozen'})
    atomic_write_json(parent/'request_ledger.json', {'request_limit':500,'requests_started':500,'balance_stop':False})
    atomic_write_json(parent/'trajectory.json', {'dataset':'fixture','topic':'topic','status':'stopped_request_limit'})
    before = sha256(parent/'request_ledger.json')
    config.update(max_api_requests=800, continuation={'snapshot':'old/trajectory.json',
        'snapshot_sha256':sha256(parent/'trajectory.json'),'ledger_sha256':before,'additional_requests':300})
    restored = continuation.prepare(tmp_path,destination,config)
    assert restored['lineage']['parent_requests'] == 500
    assert json.loads((destination/'request_ledger.json').read_text())['requests_started'] == 500
    assert sha256(parent/'request_ledger.json') == before
    with pytest.raises(ValueError,match='fresh destination'):
        continuation.prepare(tmp_path,destination,config)


class Reader:
    def __init__(self):
        self.passages = {}; self.loaded_documents=set(); self.processed=set(); self.retrieval_ranks={}
    def summary(self): return {'processed_passage_count':len(self.processed)}


def test_resume_restores_pending_passages_without_reexecuting_search(tmp_path, monkeypatch):
    reader=Reader()
    monkeypatch.setattr(coverage_pipeline,'reader_for',lambda *a: reader)
    monkeypatch.setattr(coverage_pipeline,'checkpoint',lambda *a: None)
    e={'event_id':'e1','time':'2020-01-01','summary':'The assembly approved the plan.','evidence_ids':['p1']}
    s={'dataset':'fixture','topic':'topic','timeline_events':[e]}
    memory.sync_events(s)
    memory.record_query(s, {'batch_id':1,'query':'topic agreement decision','gap_id':None,
        'result_ids':['d1'],'passage_ids':['p1','p2'],'time_filter':{'mode':'none','start':None,'end':None}})
    old={'final_events':[e],'controller_memory':s['controller_memory'],'query_batches':[],
         'candidate_pool':[], 'exploration_memory':{}, 'steps':[
             {'action':'BATCH_RETRIEVAL'}, {'action':'VERIFY','model_input':{'tool_observation':{
                 'retrieved_documents':[{'id':'p1'}]}}}, {'action':'MERGE'}]}
    atomic_write_json(tmp_path/'evidence_reader_manifest.json', {'passages':[
        {'id':'p1','document_id':'d1'},{'id':'p2','document_id':'d1'}],'processed_passage_ids':['p1']})
    resumed={'dataset':'fixture','topic':'topic'}; trace={}
    continuation.restore(resumed,trace,{'snapshot':old,'parent':tmp_path,'lineage':{}},{},None)
    assert [p['id'] for p in resumed['_pending_continuation_batch']['documents']] == ['p2']
    processed=[]
    monkeypatch.setattr(coverage_pipeline,'process_passages', lambda client,state,docs,*a:processed.extend(docs))
    continuation.finish_pending(None,resumed,{},trace,{})
    assert [p['id'] for p in processed] == ['p2']
    assert resumed['_resumed_phase1_batches'] == 1
    assert len(resumed['controller_memory']['query_history']) == 1
    assert resumed['timeline_events'] == [e]


def test_resumed_phase_one_keeps_original_round_ceiling(monkeypatch):
    state={'dataset':'fixture','topic':'topic','timeline_events':[], '_resumed_phase1_batches':2}
    calls=[]
    monkeypatch.setattr(runtime,'decide',lambda *a: (calls.append(1) or {'action':'SEARCH'},{}))
    monkeypatch.setattr(runtime,'execute_batch',lambda *a:None)
    monkeypatch.setattr(runtime,'refresh_summary',lambda *a,**k:None)
    runtime.run_phase1(None,state,{'phase1_max_rounds':3},None,{'steps':[]},{})
    assert len(calls)==1 and state['phase1_termination']=='runner_limit'


def test_invalid_gap_proof_cannot_resolve_or_abort_other_gaps():
    class Client:
        def chat(self,messages,temperature=0):
            return ChatResult(json.dumps({'action':'GAP_MEMORY','status':'RESOLVED','reason':'It seems resolved.',
                'resolved_gap_ids':['g1'],'resolution_evidence':[{'event_id':'missing'}]}),'fake',{},None)
    gap={'gap_id':'g1','status':'OPEN','left_event_id':'e1','retrieval_target':{'question':'Who approved it?'}}
    state={'dataset':'fixture','topic':'topic','timeline_events':[{'event_id':'e1','time':'2020-01-01',
        'summary':'The assembly approved it.'}], 'gap_memory':{'gaps':[gap]}}
    memory.sync_events(state)
    trace={'steps':[],'audits':[]};usage={k:0 for k in ('prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts')}
    runtime.review_gap(Client(),state,{'label_repair_attempts':1,'temperature':0},trace,usage,gap)
    assert gap['status']=='DEFERRED' and not gap['resolution_evidence']
    assert trace['steps'][-1]['observation']['completion_established'] is False
