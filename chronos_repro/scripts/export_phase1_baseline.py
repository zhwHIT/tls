"""Export only the saved first-phase execution, without Gold or new model calls."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from chronos_repro.full_timeline import apply_merge_operations


def export(path):
    hashes = {}
    def chain(p):
        p=Path(p); raw=p.read_bytes(); hashes[str(p.resolve())]=hashlib.sha256(raw).hexdigest()
        d=json.loads(raw); c=d.get('continuation')
        parents=chain(c['parent_snapshot']) if c else []
        if c and hashes[str(Path(c['parent_snapshot']).resolve())] != c['parent_snapshot_sha256']:
            raise ValueError('Parent snapshot changed')
        return parents+[(p,d)]
    lineage=chain(path)
    events=[]; pool={}; passages={}; processed=set(); queries=[]; batches=[]; boundary=None
    for p,d in lineage:
        for s in d['steps']:
            if s['phase'] != 'SKELETON_EXPLORATION':
                boundary=p; break
            inp=s.get('model_input',{});obs=s.get('observation',{})
            if s['action']=='VERIFY':
                docs=inp.get('tool_observation',{}).get('retrieved_documents',[])
                passages.update({x['id']:copy.deepcopy(x) for x in docs})
                out=s['model_output']
                if out.get('extraction_complete') and not out.get('repair_exhausted') and not obs.get('maximum_reached'):
                    processed.update(x['id'] for x in docs)
                for c in out.get('candidates',[]):
                    row=copy.deepcopy(c);row['source_passage_ids']=[x['id'] for x in docs if x['id'] in c['evidence_ids']]
                    pool[c['candidate_id']]=row
            elif s['action']=='MERGE':
                candidates=inp.get('tool_observation',{}).get('verified_candidates',[])
                fusions={x['merged_event']['event_id']:x['merged_event'] for x in obs.get('update_fusions',[])}
                operations=obs.get('applied',[])
                resolved={op['candidate_id']:fusions[op['event_id']] for op in operations if op['applied_operation']=='UPDATE'}
                if operations:
                    events,applied=apply_merge_operations(events,candidates,operations,resolved)
                    assert [(a['applied_operation'],a.get('event_id')) for a in applied]==[(a['applied_operation'],a.get('event_id')) for a in operations]
                for c in candidates:
                    if c['candidate_id'] in pool:pool[c['candidate_id']]['merge_processed']=True
                if 'event_count' in obs:assert len(events)==obs['event_count']
            elif s['action']=='BATCH_RETRIEVAL':
                rows=copy.deepcopy(obs['queries']);queries.extend(rows)
                batches.append({'batch_id':rows[0]['batch_id'],'query_count':len(rows),'continued_partial_batch':True})
        if boundary or d.get('phase1_termination'):
            boundary=p;break
    if boundary is None:
        raise ValueError('No completed first-phase boundary')
    final=lineage[-1][1]
    config=json.loads((Path(path).parent/'run_config.json').read_text(encoding='utf-8'))
    ledger=Path(path).parent/'request_ledger.json'
    return {'schema_version':1,'kind':'phase1_baseline','dataset':final['dataset'],'topic':final['topic'],
            'data':config['data'],'index':config['index'],'events':events,'candidate_pool':list(pool.values()),
            'evidence_passages':list(passages.values()),'processed_passage_ids':sorted(processed),
            'search_history':queries,'query_batches':batches,'source_hashes':hashes,'boundary_source':str(boundary),
            'prior_full_run_ledger':json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else None,
            'cost_note':'Prior run ledger includes later phases; not a phase-one cost estimate. New experiment records incremental calls separately.',
            'training_ready':False,'api_calls':0}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trajectory',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();result=export(Path(args.trajectory).resolve());out=Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(out),'events':len(result['events']),'api_calls':0}))
if __name__=='__main__':main()
