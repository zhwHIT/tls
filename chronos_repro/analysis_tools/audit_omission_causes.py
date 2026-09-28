"""Read-only supporting counts for omission diagnosis, not causal attribution."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/omission_causes_20260917'
OUT.mkdir(exist_ok=True)
hashes={}
def read(p):
    p=Path(p); raw=p.read_bytes(); hashes[str(p)]=hashlib.sha256(raw).hexdigest(); return json.loads(raw)
def chain(p):
    d=read(p); c=d.get('continuation'); parent=chain(c['parent_snapshot']) if c else []
    if c:assert hashlib.sha256(Path(c['parent_snapshot']).read_bytes()).hexdigest()==c['parent_snapshot_sha256']
    return parent+[d]
r=read(ROOT/'artifacts/phase2_gain_audit_20260917/report.json')
rows=[]
for item in r['rows']:
    lineage=chain(item['latest_path']);d=lineage[-1]
    missing=set(item['final_missing_dates'])
    passed_dates=set(); candidate_dates=set(); quarantine_dates=set()
    statuses=Counter(); drop_missing=set();drop_reasons=[]
    for t in lineage:
        for s in t['steps']:
            if s['action']=='VERIFY':
                passed=set(s.get('observation',{}).get('passed_candidate_ids',[]))
                for c in s.get('model_output',{}).get('candidates',[]):
                    date=c.get('event',{}).get('time')
                    if date in missing:
                        candidate_dates.add(date);statuses[c.get('status','unknown')]+=1
                        if c['candidate_id'] in passed:passed_dates.add(date)
            if s['action']=='MERGE':
                candidates={c['candidate_id']:c for c in s['model_input'].get('tool_observation',{}).get('verified_candidates',[])}
                for op in s.get('observation',{}).get('applied',[]):
                    c=candidates.get(op['candidate_id'],{});date=c.get('event',{}).get('time')
                    if op['applied_operation']=='DROP' and date in missing:
                        drop_missing.add(date);drop_reasons.append({'date':date,'reason':op.get('reason'),'summary':c.get('event',{}).get('summary')})
    for q in d.get('verification_quarantine',[]):
        date=q.get('raw_candidate',{}).get('event',{}).get('time')
        if date in missing:quarantine_dates.add(date)
    gaps=d.get('final_gap_memory',{}).get('gaps',[])
    reasons=Counter(g.get('status_reason','not_recorded') for g in gaps if g['status']=='DEFERRED')
    inc=d.get('incomplete_extraction',[])
    row={'dataset':item['dataset'],'topic':item['topic'],'missing_count':len(missing),
         'phase1_termination':d.get('phase1_termination'),'phase2_termination':d.get('phase2_termination'),
         'evidence_progress':d.get('evidence_progress',{}),
         'deferred_reasons':dict(reasons),
         'open_gaps_without_attempted_queries':sum(g['status']=='OPEN' and not g.get('attempted_queries') for g in gaps),
         'deferred_at_most_one_batch':sum(g['status']=='DEFERRED' and g.get('search_batches',0)<=1 for g in gaps),
         'incomplete_extraction_records':len(inc),'incomplete_reasons':dict(Counter(q.get('reason') for q in inc)),
         'quarantine_records':len(d.get('verification_quarantine',[])),
         'missing_dates_ever_in_candidate':sorted(candidate_dates),'missing_dates_ever_passed_verify':sorted(passed_dates),
         'missing_dates_in_quarantine':sorted(quarantine_dates),'missing_dates_with_merge_drop':sorted(drop_missing),
         'drop_examples':drop_reasons[:5],'missing_candidate_status_occurrences':dict(statuses)}
    rows.append(row)
summary={'topics':len(rows),'phase1_termination':dict(Counter(x['phase1_termination'] for x in rows)),
         'phase2_termination':dict(Counter(x['phase2_termination'] for x in rows))}
for field in ['known_document_count','known_passage_count','processed_passage_count','unread_passage_count']:
    summary[field]=sum(x['evidence_progress'].get(field,0) for x in rows)
for field in ['deferred_reasons','incomplete_reasons']:
    c=Counter()
    for x in rows:c.update(x[field])
    summary[field]=dict(c)
for field in ['open_gaps_without_attempted_queries','deferred_at_most_one_batch','incomplete_extraction_records','quarantine_records']:
    summary[field]=sum(x[field] for x in rows)
for field in ['missing_dates_ever_in_candidate','missing_dates_ever_passed_verify','missing_dates_in_quarantine','missing_dates_with_merge_drop']:
    summary[field]=sum(len(x[field]) for x in rows)
report={'observed_at':datetime.now().astimezone().isoformat(),'api_calls':0,'summary':summary,'rows':rows,'source_hashes':hashes,
        'limitations':['Candidate date overlaps are not semantic matches to Gold events','Processed passage is not a guarantee of exhaustive extraction; unread passages need not contain relevant Gold evidence','Quarantine and incomplete record counts can repeat candidates/passages across retries; they are not unique events or attributable losses','Latest framework code explains mechanism but trajectories include older versions']}
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
with (OUT/'report.json').open('x',encoding='utf-8') as f:json.dump(report,f,indent=2,ensure_ascii=False)
print(json.dumps(summary,indent=2))
print('DROP_EXAMPLES',json.dumps([(x['dataset'],x['topic'],x['drop_examples'][:1]) for x in rows if x['drop_examples']],ensure_ascii=True)[:3500])
