import json
from pathlib import Path
from collections import defaultdict

root=Path(__file__).resolve().parents[1]
r=root/'artifacts/tisa_v10_lowest_phase1_api'
b=json.loads((r/'David_Beckham_phase1_baseline.json').read_text(encoding='utf-8'))
d=json.loads(Path(b['boundary_source']).read_text(encoding='utf-8'))
ps={p['id']:p for p in b['evidence_passages']}
rows={}
batch=None
for s in d['steps']:
    if s['phase']!='SKELETON_EXPLORATION':break
    if s['action']=='BATCH_RETRIEVAL':
        batch=s['model_output']['batch_id']
        rows[batch]={'batch':batch,'queries':s['observation']['queries'],'events':[]}
    if s['action']=='MERGE' and batch:
        cs={c['candidate_id']:c for c in s['model_input'].get('tool_observation',{}).get('verified_candidates',[])}
        for op in s['observation'].get('applied',[]):
            if op['applied_operation']=='APPEND':
                rows[batch]['events'].append({'event_id':op['event_id'],**cs[op['candidate_id']]['event']})
lines=['# David Beckham original first phase','', 'Publication dates below describe selected evidence passages, not event dates. Events are recorded APPEND outputs, not newly fact-checked.','']
for row in rows.values():
    lines += [f"## Batch {row['batch']}",'']
    dates=set()
    for q in row['queries']:
        q['selected_articles']=[{k:ps[p][k] for k in ['id','title','publication_date']} for p in q['passage_ids'] if p in ps]
        lines += [f"- Query: `{q['query']}`",f"  - Filter: {q['executed_filter']}; retrieved documents: {len(q['result_ids'])}"]
        for p in q['selected_articles']:
            dates.add(p['publication_date'])
            lines.append(f"  - {p['publication_date']} | {p['title']} | {p['id']}")
    lines += ['', 'Events:', '']
    lines += [f"- {e['event_id']} | {e['time']} | {e['summary']}" for e in row['events']] or ['- None']
    print(json.dumps({'batch':row['batch'],'dates':sorted(dates),'events':row['events']},ensure_ascii=True))
(r/'original_phase1_search_results.md').write_text('\n'.join(lines),encoding='utf-8')
(r/'original_phase1_search_results.json').write_text(json.dumps({'source':b['boundary_source'],'batches':list(rows.values()),'final_events':b['events']},ensure_ascii=False,indent=2),encoding='utf-8')
print('TOTAL',len(rows),sum(len(x['queries']) for x in rows.values()),sum(len(x['events']) for x in rows.values()))
