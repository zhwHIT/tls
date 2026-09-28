"""Real frozen evidence + official tokenizer + scripted model, with zero API calls."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from chronos_repro.llm import ChatResult
from chronos_repro.token_budget import TokenBudgetClient
from chronos_repro.supplement import in_scope
import phase1_supplement as supplement
import batched_search_phase as batch

class Scripted:
    model='offline-scripted-not-a-quality-evaluation'
    def chat(self,messages,temperature=0):
        p=json.loads(messages[-1]['content']);stage=p['stage']
        if stage=='BATCH_POLICY':
            st=p['state'];already=st.get('recent_queries') or st.get('older_query_count')
            out={'reason':'Stop after the scripted retrieval probe.' if already else 'Probe the bounded interval using the topic.',
                 'action':'STOP' if already else 'SEARCH','queries':[] if already else [
                     {'query':st['task'].replace('_',' ')+' major events chronology',
                      'time_filter':{'mode':'none','start':None,'end':None}}],
                 'stop_reason':'NO_ACTIONABLE_QUERY' if already else None}
        elif stage=='VERIFY':out={'thought':'This scripted test adds no factual candidates.', 'candidates':[], 'extraction_complete':True}
        elif stage=='FACT_MEMORY':
            facts=[{'text':r['event']['summary'][:320],'event_ids':[r['event_id']]}
                   for r in p['event_changes'] if r.get('event')][:2]
            out={'action':'MEMORY_UPDATE','facts':facts or p['previous_facts'][:2]}
        elif stage=='GAP_DISCOVERY':out={'thought':'No questions are emitted by this scripted test.', 'action':'GAP_MEMORY','gaps':[]}
        elif stage=='FINAL_SELECT':out={'action':'SELECT','keep_event_ids':[e['event_id'] for e in p['events']],'drop':[]}
        else:raise ValueError('Unexpected mock stage: '+stage)
        return ChatResult(json.dumps(out),self.model,{'prompt_tokens':self.budget.estimate(messages),
                          'completion_tokens':0,'total_tokens':0},None,attempts=0)


def main():
    root=Path(__file__).resolve().parents[1];reports=[]
    for name in ['t17_iraq','crisis_egypt','entities_Bill_Clinton']:
        cfg=json.loads((root/f'configs/tisa_v10_{name}_supplement.json').read_text(encoding='utf-8'))
        state={'dataset':cfg['dataset'],'topic':cfg['topic'],'keywords':[cfg['topic'].replace('_',' ')],
               'timeline_events':[], 'search_history':[], 'query_batches':[],
               '_frozen_topic_path':str(root/cfg['data']/cfg['topic']/'articles.preprocessed.jsonl.gz')}
        trace={'steps':[],'audits':[]};usage={k:0 for k in ['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts']}
        fake=Scripted();client=TokenBudgetClient(fake,cfg['batch_controller']['tokens']);fake.budget=client
        supplement.load_baseline(root,state,cfg,root/cfg['index'])
        before=len(state['timeline_events'])
        supplement.run(client,state,cfg,root/cfg['index'],trace,usage)
        batch.run_phase2(client,state,cfg,root/cfg['index'],trace,usage)
        batch.finalize(client,state,cfg,trace,usage)
        checked=0
        for child in state['phase1_supplement']['children'].values():
            scope=child['trajectory']['interval']
            assert child['complete']
            for s in child['trajectory']['steps']:
                if s['action']=='VERIFY':
                    for p in s['model_input']['tool_observation']['retrieved_documents']:
                        assert p['temporal_annotations'] and all(in_scope(a['date'],scope) for a in p['temporal_annotations'])
                        checked+=1
        reports.append({'topic':name,'api_calls':0,'mock_calls':len(client.records),'max_estimated_input_tokens':max(r['estimated_prompt_tokens'] for r in client.records),
                        'baseline_events':before,'final_events':len(state['timeline_events']),
                        'intervals':state['phase1_supplement']['intervals'],'scoped_passages_checked':checked,
                        'status':state['phase1_supplement']['status'],'phase2_termination':state['phase2_termination'],
                        'quality_evaluated':False,'training_ready':False})
        print(json.dumps(reports[-1]))
    out=root/'artifacts/v10_offline_validation/mock_smoke.json'
    with out.open('x',encoding='utf-8') as f:json.dump({'api_calls':0,'scripted_responses':True,'reports':reports},f,indent=2)
if __name__=='__main__':main()
