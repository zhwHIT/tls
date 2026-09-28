"""Offline audit of saved v9.7 first phase only; no retrieval rerun or API."""
import collections
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/beckham_original_phase1_audit'

def main():
    source = ROOT / 'artifacts/tisa_v97_authorized/entities_David_Beckham/trajectory.json'
    trace = json.loads(source.read_text(encoding='utf-8'))
    baseline = json.loads((ROOT / 'artifacts/tisa_v10_lowest_phase1_api/David_Beckham_phase1_baseline.json').read_text(encoding='utf-8'))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert sha == next(iter(baseline['source_hashes'].values()))
    steps = []
    for step in trace['steps']:
        if step['phase'] != 'SKELETON_EXPLORATION':
            break
        steps.append(step)
    assert len(steps) == 246
    db = sqlite3.connect((ROOT / 'artifacts/entities_25ac73e52bc93b3b.sqlite3').as_uri() + '?mode=ro', uri=True)
    docs = {str(i): dict(id=str(i), title=title, text=text, publication_date=date) for i,title,text,date in db.execute('SELECT doc_id,title,text,timestamp FROM documents WHERE topic=?', ('David_Beckham',))}
    db.close()
    gold = json.loads((ROOT / 'snapshots/entities/25ac73e52bc93b3b/David_Beckham/timelines.jsonl').read_text(encoding='utf-8'))
    gold_dates = {x[0][:10] for x in gold}
    batches=[]; verifies=[]; candidates=[]; operations=[]; events={}
    for step in steps:
        action=step['action']
        if action=='BATCH_RETRIEVAL':
            queries=[]
            for q in step['observation']['queries']:
                queries.append({**q,'documents':[{k:v for k,v in docs[i].items() if k!='text'} for i in q['result_ids']]})
            batches.append({'step_id':step['step_id'],'queries':queries})
        if action=='VERIFY':
            passages=step['model_input']['tool_observation']['retrieved_documents']
            verifies.append({'step_id':step['step_id'],'passages':passages,'output':step['model_output'],'observation':step['observation']})
            candidates.extend({'step_id':step['step_id'],**c} for c in step['model_output']['candidates'])
        if action=='MERGE':
            obs=step['observation']
            cs={c['candidate_id']:c for c in step['model_input'].get('tool_observation',{}).get('verified_candidates',[])}
            fusions={f['merged_event']['event_id']:f['merged_event'] for f in obs.get('update_fusions',[])}
            for op in obs.get('applied',[]):
                operations.append({'step_id':step['step_id'],**op})
                if op['applied_operation']=='APPEND': events[op['event_id']]={**cs[op['candidate_id']]['event'],'event_id':op['event_id']}
                elif op['applied_operation']=='UPDATE': events[op['event_id']]=fusions[op['event_id']]
    assert {k:v['time'] for k,v in events.items()}=={e['event_id']:e['time'] for e in baseline['events']}
    queries=[q for b in batches for q in b['queries']]
    retrieved={i for q in queries for i in q['result_ids']}
    passages={p['id']:p for v in verifies for p in v['passages']}
    read_docs={p['document_id'] for p in passages.values()}
    patterns=[r'premier league.{0,100}debut|debut.{0,100}premier league',r'(?:named|appointed|made).{0,70}captain|captain.{0,70}2000',r'\bOBE\b|Order of the British Empire',r'unicef|goodwill ambassador',r'nanny|hate calls',r'Posh and Becks on the Rocks|libel',r'galaxy.{0,90}debut|debut.{0,90}galaxy',r'100th|hundredth',r'achilles',r'final.{0,60}(?:galaxy|game)|farewell|last.{0,60}galaxy',r'paris saint|PSG',r'retir',r'miami',r'unicef|fund',r'sexiest',r'miami|25th franchise']
    layers=[]
    for (date,summaries),pattern in zip(gold,patterns):
        day=date[:10]; year=int(day[:4]); hits=[]
        for d in docs.values():
            # Keyword matches are evidence leads, not established semantic matches.
            if abs(int(d['publication_date'][:4])-year)>1: continue
            matches=list(re.finditer(pattern,d['title']+'\n'+d['text'],re.I))
            if not matches:continue
            text=d['title']+'\n'+d['text']
            hits.append({'id':d['id'],'publication_date':d['publication_date'],'title':d['title'],'retrieved':d['id'] in retrieved,'read':d['id'] in read_docs,'snippets':[text[max(0,m.start()-130):m.end()+220] for m in matches[:3]]})
        hits.sort(key=lambda d:(abs((__import__('datetime').date.fromisoformat(d['publication_date'])-__import__('datetime').date.fromisoformat(day)).days),d['id']))
        layers.append({'date':day,'gold':summaries,'keyword_leads':hits,'same_publication_date':[d['id'] for d in docs.values() if d['publication_date']==day],'verify_keyword_steps':[v['step_id'] for v in verifies if re.search(pattern,' '.join(p['text'] for p in v['passages']),re.I)],'exact_candidate_dates':[c for c in candidates if c['event']['time']==day]})
    report={'scope':'Original v9.7 SKELETON_EXPLORATION, step-001 through step-246 only','source':str(source),'source_sha256':sha,'baseline_replay_equal':True,'api_calls':0,'corpus':{'documents':len(docs),'start':min(d['publication_date'] for d in docs.values()),'end':max(d['publication_date'] for d in docs.values())},'counts':{'batches':len(batches),'queries':len(queries),'result_occurrences':sum(len(q['result_ids']) for q in queries),'retrieved_documents':len(retrieved),'selected_unique_passages':len(passages),'read_documents':len(read_docs),'verify_steps':len(verifies),'candidates':len(candidates),'candidate_status':dict(collections.Counter(c['status'] for c in candidates)),'merge_operations':dict(collections.Counter(o['applied_operation'] for o in operations)),'events':len(events),'gold_hits':len({e['time'] for e in events.values()}&gold_dates)},'stop':next(s for s in steps if s['action']=='STOP'),'retrieved_publication_years':dict(sorted(collections.Counter(docs[i]['publication_date'][:4] for i in retrieved).items())),'read_publication_years':dict(sorted(collections.Counter(docs[i]['publication_date'][:4] for i in read_docs).items())),'batches':batches,'verifies':verifies,'candidates':candidates,'merge_operations':operations,'events':baseline['events'],'gold_layers':layers,'limitations':'Keyword and publication-date overlap are diagnostic leads, not proof of event/date support. Saved model_input is not independently authenticated wire input.'}
    OUT.mkdir(exist_ok=True)
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Original Beckham first-phase queries and results','',report['scope'],'','Each result date below is publication date, NOT event date.','']
    for n,b in enumerate(batches,1):
        lines += [f'## Batch {n} / {b["step_id"]}','']
        for q in b['queries']:
            lines += [f'### {q["query"]}',f'Time filter: `{q["executed_filter"]}`','', '|Document|Publication date|Title|Selected passages|','|---|---|---|---|']
            for d in q['documents']:
                ids=[p for p in q['passage_ids'] if p.split('@')[0]==d['id']]
                lines.append(f'|{d["id"]}|{d["publication_date"]}|{d["title"].replace("|","/")}|{"; ".join(ids)}|')
            lines.append('')
    lines += ['## Original first-phase events','','|ID|Event date|Summary|','|---|---|---|']
    lines += [f'|{e["event_id"]}|{e["time"]}|{e["summary"]}|' for e in baseline['events']]
    (OUT/'queries_and_events.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['counts','corpus','read_publication_years']},ensure_ascii=False))
    for row in layers:
        print(row['date'],json.dumps(row['keyword_leads'][:2],ensure_ascii=False))

if __name__=='__main__':main()
