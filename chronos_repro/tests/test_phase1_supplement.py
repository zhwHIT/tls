import copy
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batched_search_phase as batch
import phase1_supplement as runtime
import run_tisa_two_phase_annotation as runner
import coverage_pipeline
import date_conflict_review
from chronos_repro.supplement import choose_intervals, fresh_worker, in_scope, intersect, gap_exhausted, validate_settings
from chronos_repro.evidence_access import EvidenceReader
from chronos_repro.full_timeline import apply_merge_operations
from chronos_repro.multitopic_evaluation import summarize_topics
from chronos_repro.verification_efficiency import extraction_key


def event(i='event-001', day='2020-01-03', summary='Aurora approved the new development agreement.'):
    return {'event_id':i,'time':day,'summary':summary,'actors':[],'location':None,
            'evidence_ids':['p1'],'confidence':.9,'conflict':False,
            'date_evidence':{'document_id':'p1','quote':'Approval was granted on January 3.'}}


def passage(i='p1', day='2020-01-03'):
    return {'id':i,'document_id':i,'title':'Aurora news',
            'text':'Aurora. Approval was granted on January 3. This retrospective was published years later.',
            'publication_date':'2025-01-01','content_sha256':i,
            'temporal_annotations':[{'date':day}]}


def config():
    return {'phase1_supplement':{'enabled':True,'workers':1,'interval_days':30,'max_intervals':2,
                               'min_documents':1,'batches_per_interval':2,'reread_passages':2,'reread_pages':2},
            'coverage_pipeline':{'enabled':True,'passages_per_search':2},
            'batch_controller':{'enabled':True,'max_queries':3},'phase2_teacher_guidance':False,
            'phase2_max_batches_per_gap':3,'phase2_max_gap_cycles':30,'phase1_max_rounds':24,
            'label_repair_attempts':1,'temperature':0}


@pytest.mark.parametrize('day,expected',[('2020-01-01',True),('2020-01-31',True),('2019-12-31',False),('2020-02-01',False),(None,False),('2020-01',False)])
def test_event_scope_edges(day,expected):
    assert in_scope(day,{'start':'2020-01-01','end':'2020-01-31'}) is expected


def test_intersection_never_expands_or_reverts_to_unbounded():
    scope={'start':'2020-01-01','end':'2020-01-31'}
    assert intersect(scope,{'mode':'none','start':None,'end':None})['end']=='2020-01-31'
    assert intersect(scope,{'start':'2019-01-01','end':'2020-01-15'})['start']=='2020-01-01'
    assert intersect(scope,{'start':'2021-01-01','end':'2021-02-01'}) is None


def test_partition_uses_evidence_and_does_not_choose_long_silence():
    rows=choose_intervals([event(day='1950-01-01')],[passage(),passage('p2','2020-04-01')],
                          min_documents=1,maximum=2,width_days=30,keywords=['Aurora'])
    assert len(rows)==2 and all(r['start']>'2019-01-01' for r in rows)
    assert rows[0]['end']<rows[1]['start']
    assert choose_intervals([],[],min_documents=1)==[]


def test_duplicate_content_does_not_inflate_interval_support():
    a=passage();b=passage('p2');b['content_sha256']=a['content_sha256']
    assert choose_intervals([], [a,b], min_documents=2)==[]


def test_workers_start_empty_and_detached():
    parent={'dataset':'entities','topic':'Aurora','keywords':['Aurora'],'timeline_events':[event()],
            'controller_memory':{'facts':['secret old summary']}}
    child=fresh_worker(parent,{'start':'2020-01-01','end':'2020-01-31'})
    assert not child['timeline_events'] and not child['search_history'] and not child['gap_memory']['gaps']
    assert 'controller_memory' not in child
    child['keywords'].append('new');assert parent['keywords']==['Aurora']


def test_event_scoped_retrieval_keeps_retrospective_and_excludes_other_dates():
    reader=EvidenceReader('unused','Aurora',{'passages_per_search':2})
    reader.passages={'p1':passage(),'p2':passage('p2','2019-01-03')}
    docs=reader.select('Aurora',temporal={'event_scope':{'start':'2020-01-01','end':'2020-01-31'}})
    assert [d['id'] for d in docs]==['p1']
    assert docs[0]['publication_date']=='2025-01-01'
    assert len(reader.passages['p2']['temporal_annotations'])==1


def test_scoped_policy_has_no_publication_filter_contradiction():
    text=batch.policy_system({'_event_scope':{'start':'2020-01-01','end':'2020-01-31'}})
    assert 'EVENT OCCURRENCE' in text and 'applies to ARTICLE PUBLICATION' not in text
    assert batch.policy_system({})==batch.POLICY_SYSTEM


def test_disjoint_model_range_never_calls_global_search(monkeypatch):
    monkeypatch.setattr(batch,'search',lambda *a,**k:pytest.fail('Unbounded search invoked'))
    monkeypatch.setattr(coverage_pipeline,'process_passages',lambda *a,**k:None)
    state=fresh_worker({'dataset':'x','topic':'Aurora'}, {'start':'2020-01-01','end':'2020-01-31'})
    state['_evidence_reader']=EvidenceReader('unused','Aurora',{})
    trace={'steps':[],'audits':[]}
    batch.execute_batch(None,state,config(),'unused',trace,{},batch.SKELETON,
        {'queries':[{'query':'Aurora later milestone','time_filter':{'mode':'hard','start':'2021-01-01','end':'2021-01-31'}}]}, {})
    assert state['search_history'][0]['executed_filter'] is None
    assert not state['search_history'][0]['result_ids']


def test_scope_rejected_at_merge_boundary():
    c={'candidate_id':'c','event':{'time':'2021-01-01'}}
    with pytest.raises(ValueError,match='out-of-scope'):
        runner.execute_merge(None,{'_event_scope':{'start':'2020-01-01','end':'2020-01-31'}},[c],{}, {}, {}, 'S',{})


def test_gap_budget_counts_batches_not_queries():
    gap={'attempted_queries':['q1','q2','q3'],'search_batches':1}
    assert not gap_exhausted(gap,config())
    assert gap_exhausted(gap,{'phase2_max_attempts_per_gap':3})
    gap['search_batches']=3;assert gap_exhausted(gap,config())


def test_serial_ledger_guard_is_explicit():
    c=config();validate_settings(c);c['phase1_supplement']['workers']=2
    with pytest.raises(ValueError,match='serial'):validate_settings(c)


def test_new_id_after_deletion_does_not_reuse_surviving_id():
    old=[event('event-003')]; c={'candidate_id':'c','status':'SUPPORTED','event':{'time':'2020-02-02','summary':'A different event was announced.','actors':[],'location':None},'evidence_ids':['p1'],'confidence':.9}
    result,_=apply_merge_operations(old,[c],[{'candidate_id':'c','operation':'APPEND'}])
    assert result[-1]['event_id']=='event-004'


def test_cache_keys_distinguish_extraction_mode_and_scope():
    c=config();base={'events':[],'extraction_mode':'coarse'}
    assert extraction_key([],base,c,6)!=extraction_key([],{**base,'extraction_mode':'detailed'},c,6)
    assert extraction_key([],base,c,6)!=extraction_key([],{**base,'event_scope':{'start':'2020-01-01','end':'2020-01-31'}},c,6)


@pytest.mark.parametrize('decision',['EXISTING','CANDIDATE','DIFFERENT','DEFER'])
def test_date_conflict_decisions_require_evidence_and_keep_provenance(monkeypatch,decision):
    e=event(); candidate={'candidate_id':'c','status':'SUPPORTED','event':{k:e[k] for k in ('time','summary','actors','location')},
                         'evidence_ids':['p2'],'confidence':.8,'date_evidence':{'document_id':'p2','quote':'Approval was granted on January 4.'}}
    candidate['event']['time']='2020-01-04'
    reader=EvidenceReader('unused','Aurora',{});reader.passages={'p1':passage(),'p2':{**passage('p2'),'text':'Approval was granted on January 4.'}}
    state={'timeline_events':[e],'_evidence_reader':reader}
    def call(client,system,payload,validator,cfg):
        support=[{'side':p['side'],'quote':p['quote']} for p in payload['evidence']]
        return validator({'decision':decision,'reason':'Evidence was compared.', 'support':support}),[]
    monkeypatch.setattr(runner,'repaired_call',call)
    pending,ops,fused=date_conflict_review.partition(None,state,[candidate],{'date_conflict_review':{'enabled':True}}, {'steps':[],'audits':[]}, {}, 'S')
    assert not pending
    if decision in {'EXISTING','CANDIDATE'}:
        assert fused['c']['time']==('2020-01-03' if decision=='EXISTING' else '2020-01-04')
        assert fused['c']['evidence_ids']==['p1','p2']
    elif decision=='DIFFERENT':assert ops[0]['operation']=='APPEND'
    else:assert state['date_conflict_quarantine'][0]['candidate']['candidate_id']=='c'
    assert e['time']=='2020-01-03'


def test_single_topic_evaluation_is_supported():
    row={'dataset':'x','topic':'a','status':'ok','gold_date_hits':1,'gold_date_count':2,'predicted_date_count':1}
    report=summarize_topics([row],[('x','a')])
    assert report['all_complete'] and report['aggregate']['micro_gold_date_recall']==.5


def test_failed_date_adjudication_is_quarantined_instead_of_discarded(monkeypatch):
    e=event();candidate={'candidate_id':'c','status':'SUPPORTED',
        'event':{'time':'2020-01-04','summary':e['summary']},'evidence_ids':['p2'],
        'date_evidence':{'document_id':'p2','quote':'A different explicit date.'}}
    reader=EvidenceReader('unused','Aurora',{});reader.passages={'p1':passage(),'p2':{**passage('p2'),'text':'A different explicit date.'}}
    st={'timeline_events':[e],'_evidence_reader':reader}
    def fail(*a,**k):raise runner.LabelValidationError('invalid quoted date',{},[])
    monkeypatch.setattr(runner,'repaired_call',fail)
    _,ops,_=date_conflict_review.partition(None,st,[candidate],{'date_conflict_review':{'enabled':True}}, {'steps':[],'audits':[]},{},'S')
    assert ops[0]['operation']=='DROP' and st['date_conflict_quarantine']
    assert e['conflict'] and e['time']=='2020-01-03'


@pytest.mark.parametrize('candidate_day',['2021-01-03','2020-01-20'])
def test_verify_out_of_interval_is_quarantined_before_merge(monkeypatch,candidate_day):
    state=fresh_worker({'dataset':'x','topic':'Aurora'}, {'start':'2020-01-01','end':'2020-01-31'})
    reader=EvidenceReader('unused','Aurora',{});reader.passages={'p1':passage()};state['_evidence_reader']=reader
    c={'candidate_id':'bad-date','status':'SUPPORTED','event':{'time':candidate_day,'summary':'Aurora completed a new agreement.','actors':[],'location':None},
       'evidence_ids':['p1'],'date_evidence':{'document_id':'p1'},'confidence':.9,'relevance_pass':True,'contribution_pass':True}
    monkeypatch.setattr(runner,'verify_batch',lambda *a:({'candidates':[copy.deepcopy(c)],'extraction_complete':True},[]))
    received=[]
    def merge(client,st,cs,*args):
        received.extend(cs);return {'operations':[]},[]
    monkeypatch.setattr(runner,'execute_merge',merge)
    doc={**passage(),'event_scope':{'start':'2020-01-01','end':'2020-01-10'}}
    coverage_pipeline.process_passages(None,state,[doc],config(),{'steps':[],'audits':[]},{},batch.SKELETON,{'events':[]})
    assert not received and not state['timeline_events']
    assert state['scope_quarantine'][0]['event']['time']==candidate_day


def test_reread_deduplicates_and_skips_validation_failures(monkeypatch):
    cfg=config();st={'dataset':'x','topic':'Aurora','timeline_events':[],'keywords':['Aurora'],
                    'search_history':[],'incomplete_extraction':[
                        {'passage_ids':['p1'],'reason':'extraction page limit reached'},
                        {'passage_ids':['p1'],'reason':'extraction page limit reached'},
                        {'passage_ids':['p2'],'reason':'unresolved validation'}]}
    reader=EvidenceReader('unused','Aurora',{});reader.passages={'p1':passage(),'p2':passage('p2')};st['_evidence_reader']=reader
    monkeypatch.setattr(runtime,'populate_reader',lambda *a:None)
    monkeypatch.setattr(runtime,'choose_intervals',lambda *a,**k:[])
    monkeypatch.setattr(batch,'refresh_summary',lambda *a,**k:None)
    seen=[]
    def process(client,state,docs,*args):
        assert state['_reread_pages']==2
        seen.extend(p['id'] for p in docs)
    monkeypatch.setattr(coverage_pipeline,'process_passages',process)
    runtime.run(None,st,cfg,'unused',{'steps':[],'audits':[]},{})
    assert seen==['p1'] and '_reread_pages' not in st
    runtime.run(None,st,cfg,'unused',{'steps':[],'audits':[]},{})
    assert seen==['p1']


@pytest.mark.parametrize('interrupt',[False,True])
def test_supplement_integration_is_resumable_and_idempotent(monkeypatch,interrupt):
    c=config();c['phase1_supplement']['max_intervals']=1
    state={'dataset':'x','topic':'Aurora','keywords':['Aurora'],'timeline_events':[],
           'search_history':[],'query_batches':[],'phase1_termination':'runner_limit'}
    reader=EvidenceReader('unused','Aurora',{'passages_per_search':2});reader.passages={'p1':passage()}
    state['_evidence_reader']=reader;trace={'steps':[],'audits':[]};calls=[]
    monkeypatch.setattr(runtime,'populate_reader',lambda r:None)
    monkeypatch.setattr(batch,'refresh_summary',lambda *a,**k:None)
    def local(client,child,cfg,index,t,u):
        calls.append(1)
        assert not child.get('controller_memory',{}).get('facts')
        child['timeline_events']=[event()]
        child['query_batches']=[{'batch_id':1,'query_count':1}]
        if interrupt and len(calls)==1:raise ValueError('simulated interruption')
        child['phase1_termination']='autonomous_stop'
    monkeypatch.setattr(batch,'run_phase1',local)
    monkeypatch.setattr(runner,'decide_merge',lambda cl,st,cs,cf:({'operations':[{'candidate_id':x['candidate_id'],'operation':'APPEND','target_event_id':None,'reason':'new'} for x in cs]},[]))
    if interrupt:
        with pytest.raises(ValueError,match='simulated'):runtime.run(None,state,c,'unused',trace,{})
        assert not state['timeline_events']
        assert state['phase1_supplement']['children']
    runtime.run(None,state,c,'unused',trace,{})
    assert len(state['timeline_events'])==1 and state['phase1_supplement']['status']=='complete'
    before=copy.deepcopy(state['timeline_events']);runtime.run(None,state,c,'unused',trace,{})
    assert state['timeline_events']==before
