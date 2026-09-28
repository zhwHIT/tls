"""Fixed-query, offline passage intervention. No API, model generation or index writes."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chronos_repro.evidence_access import split_passages, _passage, terms
from tokenizers import Tokenizer

OUT = ROOT / 'artifacts/beckham_passage_selection_offline'
PROJECTION = ('id', 'document_id', 'title', 'publication_date', 'text', 'context_before')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run():
    trace_path = ROOT / 'artifacts/tisa_v97_authorized/entities_David_Beckham/trajectory.json'
    audit_path = ROOT / 'artifacts/beckham_original_phase1_audit/report.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    assert digest(trace_path) == audit['source_sha256']
    source_hashes = {str(trace_path): digest(trace_path), str(audit_path): digest(audit_path)}
    dbpath = ROOT / 'artifacts/entities_25ac73e52bc93b3b.sqlite3'
    con = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True)
    docs = {str(i):dict(id=str(i),title=t,text=x,publication_date=d) for i,t,x,d in con.execute('SELECT doc_id,title,text,timestamp FROM documents WHERE topic=?', ('David_Beckham',))}
    con.close()
    tokenizer_path = ROOT / 'artifacts/deepseek_v4_tokenizer/tokenizer.json'
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    def tokens(p):
        return len(tokenizer.encode(json.dumps({k:p[k] for k in PROJECTION},ensure_ascii=False,separators=(',',':')),add_special_tokens=False).ids)
    def recorded(pid):
        did,tail = pid.split('@'); _,start,end = tail.split(':')
        p = _passage(docs[did],docs[did]['text'],int(start),int(end))
        assert p['id'] == pid, 'Frozen body hash/offset mismatch'
        return p
    queries = []
    for batch_index,b in enumerate(audit['batches'],1):
        for q in b['queries']:
            queries.append({**q,'query_number':len(queries)+1,'batch_number':batch_index,'baseline':[recorded(pid) for pid in q['passage_ids']]})
    assert len(queries)==51 and all(len(q['baseline'])==2 for q in queries)
    available_ids={i for q in queries for i in q['result_ids']} | {p['document_id'] for q in queries for p in q['baseline']}
    leads = {i:split_passages(docs[i],180,1200,30)[0] for i in sorted(available_ids) if docs[i]['text'].strip()}
    def fit(p, original):
        # Same per-slot evidence-text token and character ceilings as the recorded slot.
        limit=tokens(original); max_body=len(original['text']); max_visible=len(original['text'])+len(original['context_before'])
        p=copy.deepcopy(p)
        while tokens(p)>limit or len(p['text'])>max_body or len(p['text'])+len(p['context_before'])>max_visible:
            if len(p['text'])<80:
                return copy.deepcopy(original), True
            body=p['text'];cut=body.rfind(' ',0,len(body)-8)
            if cut<0: return copy.deepcopy(original), True
            p=_passage(docs[p['document_id']],docs[p['document_id']]['text'],p['source_start'],p['source_start']+cut)
        return p,False

    # Label-free policy: same documents' leads, or ranked unseen current-result lead + one original slot.
    methods={'recorded':[], 'same_documents_leads':[], 'top12_lead_plus_original':[]}
    seen_leads=set()
    for q in queries:
        original=q['baseline'];methods['recorded'].append(copy.deepcopy(original))
        same=[fit(leads[p['document_id']],p)[0] for p in original]
        if same[0]['id']==same[1]['id']:same[1]=copy.deepcopy(original[1])
        methods['same_documents_leads'].append(same)
        pool=[leads[i] for i in q['result_ids'] if i in leads]
        ts={p['id']:terms(p['title']+' '+p['text']) for p in pool}
        df=Counter(t for s in ts.values() for t in s)
        qt=terms(q['query']);idf={t:math.log(1+len(pool)/(1+df[t])) for t in qt}
        ranks={i:n+1 for n,i in enumerate(q['result_ids'])}
        def score(p):
            lexical=sum(idf[t] for t in qt & ts[p['id']])/max(1,sum(idf.values()))
            return (p['document_id'] not in seen_leads,0.7*lexical+0.3/ranks[p['document_id']],-ranks[p['document_id']])
        lead=max(pool,key=score)
        pair=[fit(lead,original[0])[0],copy.deepcopy(original[1])]
        if pair[0]['id']==pair[1]['id']:
            pair=[copy.deepcopy(original[0]),fit(lead,original[1])[0]]
        if pair[0]['source_start']==0:seen_leads.add(pair[0]['document_id'])
        methods['top12_lead_plus_original'].append(pair)
    # Policies have finished; evaluation labels below never participate in selection.
    label_specs=[
        ('retirement_18406','2013-05-16','18406','event','David Beckham has announced the end of his glittering career'),
        ('retirement_18348','2013-05-16','18348','event','David Beckham has announced his retirement from football'),
        ('retirement_18349','2013-05-16','18349','event','David Beckham is to retire from football'),
        ('cap100_event','2008-03-26','19367','event','David Beckham made his 100th appearance for England last night'),
        ('cap100_date','2008-03-26','19367','relative_date','David Beckham made his 100th appearance for England last night'),
        ('galaxy_debut','2007-07-21','19197','event',"David Beckham 's LA Galaxy debut"),
        ('galaxy_date','2007-07-21','19197','relative_date',"Beckham 's appearance before the screaming crowds at the Home Depot Center on Saturday night"),
        ('psg_preview','2013-01-31','18446','preview','David Beckham is to return to elite European football after flying to France to sign a deal with Paris Saint - Germain'),
        ('galaxy_farewell_preview','2012-12-01','18462','preview',"Beckham will leave the Galaxy on a high regardless of the result in Saturday 's final against the Houston Dynamo"),
    ]
    labels=[]
    for name,date,did,kind,quote in label_specs:
        start=docs[did]['text'].index(quote)
        labels.append(dict(name=name,gold_date=date,document_id=did,kind=kind,quote=quote,start=start,end=start+len(quote),publication_date=docs[did]['publication_date']))
    # Existing SUPPORTED evidence retention measures coverage, not semantic correctness.
    retention=[];unlocated=[]
    for c in audit['candidates']:
        if c['status']!='SUPPORTED':continue
        e=c.get('date_evidence') or {};pid=e.get('document_id','');did=pid.split('@')[0];quote=e.get('quote','')
        if did not in docs or not quote or quote not in docs[did]['text']:
            unlocated.append(c['candidate_id']);continue
        start=docs[did]['text'].index(quote)
        retention.append(dict(candidate_id=c['candidate_id'],document_id=did,start=start,end=start+len(quote),quote=quote))
    def covered(label,p):
        return p['document_id']==label['document_id'] and p['source_start']-len(p['context_before'])<=label['start'] and p['source_end']>=label['end']
    results={};plans={}
    for method,pairs in methods.items():
        rows=[];flat=[]
        for q,pair in zip(queries,pairs):
            assert len(pair)==2
            for new,old in zip(pair,q['baseline']):
                assert tokens(new)<=tokens(old)
                assert len(new['text'])<=len(old['text'])
                assert len(new['text'])+len(new['context_before'])<=len(old['text'])+len(old['context_before'])
                assert docs[new['document_id']]['text'][new['source_start']:new['source_end']]==new['text']
                assert new['document_id'] in q['result_ids'] or new['document_id'] in {p['document_id'] for p in q['baseline']}
            flat.extend(pair)
            rows.append(dict(query_number=q['query_number'],batch_number=q['batch_number'],query=q['query'],budget_tokens=sum(tokens(p) for p in q['baseline']),selected_tokens=sum(tokens(p) for p in pair),passages=pair,label_hits=[l['name'] for l in labels if any(covered(l,p) for p in pair)]))
        label_hits={l['name']:[row['query_number'] for row in rows if l['name'] in row['label_hits']] for l in labels}
        event_dates=sorted({l['gold_date'] for l in labels if l['kind']=='event' and label_hits[l['name']]})
        date_dates=sorted({l['gold_date'] for l in labels if l['kind']=='relative_date' and label_hits[l['name']] and l['gold_date'] in event_dates})
        retained=[l['candidate_id'] for l in retention if any(covered(l,p) for p in flat)]
        results[method]=dict(query_count=len(rows),passage_occurrences=len(flat),unique_passages=len({p['id'] for p in flat}),unique_documents=len({p['document_id'] for p in flat}),evidence_projection_tokens=sum(row['selected_tokens'] for row in rows),body_context_chars=sum(len(p['text'])+len(p['context_before']) for p in flat),event_evidence_dates=event_dates,relative_date_evidence_dates=date_dates,preview_hits=[l['name'] for l in labels if l['kind']=='preview' and label_hits[l['name']]],label_hits=label_hits,retained_supported_candidate_quotes=retained,retained_count=len(retained),retention_denominator=len(retention))
        plans[method]=rows
    # Historical wire authenticity and closed-loop outcomes are deliberately not inferred.
    report=dict(observed_at=datetime.now(timezone.utc).isoformat(),scope='v9.7 first-phase frozen 51 queries/results, step-001 through step-246',api_calls=0,policy_version='preregistered_single_pass_v1_no_outcome_tuning',source_hashes=source_hashes,tokenizer_sha256=digest(tokenizer_path),script_sha256=digest(Path(__file__)),budget_projection=list(PROJECTION),budget_note='Per recorded slot: same token, body-char and body+context-char ceiling; two slots. This is evidence-text payload only, not full VERIFY requests or actual cost.',policies={'recorded':'Saved selection, not rerunning current reader.','same_documents_leads':'Replace each selected document passage with its first chunk; keep original slot when duplicate or cannot fit.','top12_lead_plus_original':'From current original top12 choose unseen lead first; then 0.7 normalized query-IDF overlap + 0.3 reciprocal document rank. Replace slot1, keep slot2; swap slots if duplicate. No Gold/date labels in policy.'},diagnostic_labels=labels,diagnostic_label_scope='Three manually identified Gold events already present in retrieved texts, plus two preview leads. Not exhaustive qrels, not blind test, not final Gold recall.',retention_unlocated_candidate_ids=unlocated,results=results,limitations=['Queries and original retrieval are held fixed despite changed passages; no adaptive policy rollout.','Gold-event labels evaluate span availability, not extraction or exact-date correctness.','Preview leads are not completed events.','SUPPORTED retention is historical quote retention, not correctness; duplicates remain in candidate denominator.','No full-prompt 3800/4096 check; model state and annotations were not regenerated.'],checks={'source_body_offsets':True,'per_slot_tokens_and_chars':True,'exactly_two_passages_per_query':True,'original_trace_unchanged':digest(trace_path)==source_hashes[str(trace_path)]})
    OUT.mkdir(exist_ok=True)
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'selection_plans.json').write_text(json.dumps(plans,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,res in results.items():print(name,json.dumps({k:v for k,v in res.items() if k not in ['label_hits','retained_supported_candidate_quotes']},ensure_ascii=False))
    print('labels',json.dumps({m:r['label_hits'] for m,r in results.items()},ensure_ascii=False))
    print('unlocated_retention',unlocated)

if __name__=='__main__':run()
