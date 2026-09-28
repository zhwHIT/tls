"""Offline, label-free sentence selection over frozen original-phase retrieval.

Inspired by DATEWISE's union of opening and date-mention sentences; this is
not a reproduction of PM-MEAN or a live VERIFY pipeline.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chronos_repro.evidence_access import terms
from tokenizers import Tokenizer

OUT = ROOT / 'artifacts/beckham_sentence_queue_offline'
DATE_CUE = re.compile(
    r'\b(?:19|20)\d{2}\b|\b(?:today|yesterday|tomorrow|last night)\b|'
    r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|'
    r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b', re.I)
POLICY = {
    'name': 'datewise_inspired_sentence_union_v1',
    'lanes': ['original_related', 'opening_main_candidate', 'date_mention'],
    'cumulative_token_targets': [0.45, 0.75, 1.0],
    'opening_sentences': 5,
    'sentence_unit': 'Frozen body non-empty newline units; fragmented/short units attach to next unit. Not a new NLP parser.',
    'context': 'Include preceding unit for a leading pronoun or an incomplete preceding unit.',
    'main_score': {'topic': 0.45, 'title': 0.30, 'query': 0.15, 'rank': 0.10},
    'date_score': {'query': 0.50, 'topic': 0.20, 'date_cue': 0.20, 'rank': 0.10},
    'original_score': {'query': 0.50, 'date_cue': 0.25, 'topic': 0.15, 'position': 0.10},
    'queue_age_bonus': '0.10 * min(age_in_queries,8)/8',
    'selected_lexical_redundancy_penalty': 0.25,
    'date_note': 'Lexical date cues select candidates; no date normalization or publication-date substitution.',
    'queue_note': 'Read means visible in this offline plan, not verified. Preserve unselected source spans; no expiration.',
    'budget_note': 'Recompute all arms in identical source-bundle JSON. Per-query token and visible-character ceilings equal original; full source metadata and segment boundaries counted.',
    'outcome_note': 'Single development-topic diagnostic; no full-agent rollout, no formal preregistration.',
}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def merge_ranges(ranges):
    out = []
    for a,b in sorted(set(map(tuple,ranges))):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1],b)
        else:
            out.append([a,b])
    return out

def contains(ranges, a, b):
    return any(left <= a and right >= b for left,right in merge_ranges(ranges))

def visible_ranges(passages):
    out = defaultdict(list)
    for p in passages:
        out[p['document_id']].append([p['source_start']-len(p['context_before']),p['source_end']])
    return dict(out)

def sentence_candidates(doc):
    # Use existing source line boundaries, retain offsets and do not concatenate gaps.
    units = []
    pending = None
    for m in re.finditer(r'[^\n]+',doc['text']):
        raw=m.group(); a=m.start()+len(raw)-len(raw.lstrip()); b=m.end()-len(raw)+len(raw.rstrip())
        if a>=b:continue
        if pending is not None:a=pending;pending=None
        if len(doc['text'][a:b].split())<4:
            pending=a;continue
        units.append((a,b))
    if pending is not None:
        if units:units[-1]=(units[-1][0],len(doc['text'].rstrip()))
        else:units=[(pending,len(doc['text'].rstrip()))]
    result=[]
    for n,(a,b) in enumerate(units):
        text=doc['text'][a:b];start=a
        if n:
            prior=doc['text'][units[n-1][0]:units[n-1][1]]
            if re.match(r'^(?:He|She|It|They|His|Her|Their|This|That|These|Those)\b',text,re.I) or not re.search(r'[.!?][\s\"\u201d\u2019]*$',prior):
                start=units[n-1][0]
        result.append({'id':f'{doc["id"]}:{a}:{b}', 'document_id':doc['id'],
                       'core_start':a,'core_end':b,'start':start,'end':b,'ordinal':n,
                       'opening':n<POLICY['opening_sentences'],'has_date':bool(DATE_CUE.search(text)),
                       'terms':terms(text),'title_terms':terms(doc['title'])})
    return result

class Selector:
    def __init__(self, docs, candidates, tokenizer):
        self.docs,self.candidates,self.tokenizer=docs,candidates,tokenizer
        self.doc_hashes={i:hashlib.sha256(d['text'].encode()).hexdigest() for i,d in docs.items()}

    def payload(self, ranges):
        out=[]
        for did in sorted(ranges):
            if not ranges[did]:continue
            d=self.docs[did]
            out.append({'document_id':did,'document_sha256':self.doc_hashes[did],
                        'title':d['title'],'publication_date':d['publication_date'],
                        'segments':[{'source_start':a,'source_end':b,'text':d['text'][a:b]}
                                    for a,b in merge_ranges(ranges[did])]})
        return out

    @lru_cache(maxsize=16384)
    def encoded(self, wire):
        return len(self.tokenizer.encode(wire,add_special_tokens=False).ids)

    def cost(self, ranges):
        payload=self.payload(ranges)
        wire=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        return self.encoded(wire),sum(len(s['text']) for d in payload for s in d['segments'])

    def run(self, queries, persistent):
        first_seen={};ranks={};read=defaultdict(list);rows=[];pending={};skips=Counter()
        for index,q in enumerate(queries,1):
            current=set(q['result_ids'])
            original=visible_ranges(q['baseline'])
            newly_seen=0
            for rank,did in enumerate(q['result_ids'],1):
                ranks[did]=min(ranks.get(did,rank),rank)
                if did not in first_seen:
                    first_seen[did]=index
                    for c in self.candidates.get(did,[]):pending[c['id']]=c;newly_seen+=1
            assert set(original)<=set(first_seen), 'Original reuse refers to an unavailable future document'
            eligible_docs=set(first_seen) if persistent else current | set(original)
            budget_tokens,budget_chars=self.cost(original)
            selected={};chosen=[];covered_terms=[]
            pool=[c for c in pending.values() if c['document_id'] in eligible_docs]
            qt=terms(q['query']);topic={'david','beckham'}
            def sim(left,right):return len(left & right)/max(1,len(left | right))
            def eligible(c,lane):
                if lane=='original_related':return contains(original.get(c['document_id'],[]),c['core_start'],c['core_end'])
                if lane=='opening_main_candidate':return c['opening']
                return c['has_date']
            def score(c,lane):
                t=c['terms'];query=len(t & qt)/max(1,len(qt));top=len(t & topic)/2
                rank=1/ranks.get(c['document_id'],12)
                if lane=='original_related':s=.5*query+.25*c['has_date']+.15*top+.1/(1+c['ordinal'])
                elif lane=='opening_main_candidate':s=.45*top+.30*sim(t,c['title_terms'])+.15*query+.10*rank
                else:s=.50*query+.20*top+.20*c['has_date']+.10*rank
                age=min(index-first_seen[c['document_id']],8)/8
                redundancy=max((sim(t,other) for other in covered_terms),default=0)
                return (s+.10*age-.25*redundancy,-first_seen[c['document_id']],-c['ordinal'],c['id'])
            def add_one(lane, ceiling):
                eligible_rows=[c for c in pool if eligible(c,lane) and not contains(selected.get(c['document_id'],[]),c['core_start'],c['core_end'])]
                for c in sorted(eligible_rows,key=lambda c:score(c,lane),reverse=True):
                    proposal={i:list(v) for i,v in selected.items()}
                    proposal.setdefault(c['document_id'],[]).append([c['start'],c['end']])
                    count,chars=self.cost(proposal)
                    if count>ceiling or chars>budget_chars:
                        skips[c['id']]+=1;continue
                    selected.clear();selected.update(proposal)
                    chosen.append({'candidate_id':c['id'],'lane':lane,'from_previous_query':first_seen[c['document_id']]<index,
                                   'outside_current_top12':c['document_id'] not in current})
                    covered_terms.append(c['terms'])
                    return True
                return False
            for lane,fraction in zip(POLICY['lanes'],POLICY['cumulative_token_targets']):
                while add_one(lane,int(budget_tokens*fraction)):pass
            # Leftover capacity may serve any lane; source segments are never clipped to fit.
            while True:
                changed=False
                for lane in POLICY['lanes']:
                    if add_one(lane,budget_tokens):changed=True
                if not changed:break
            for did,spans in selected.items():read[did]=merge_ranges(read[did]+spans)
            delivered=[key for key,c in pending.items() if contains(read[c['document_id']],c['core_start'],c['core_end'])]
            for key in delivered:del pending[key]
            count,chars=self.cost(selected)
            rows.append({'query_number':index,'query':q['query'],'current_result_ids':q['result_ids'],
                         'token_budget':budget_tokens,'visible_char_budget':budget_chars,'tokens':count,'visible_chars':chars,
                         'payload':self.payload(selected),'choices':chosen,'new_candidate_count':newly_seen,
                         'newly_covered_candidate_count':len(delivered),'unread_candidate_count':len(pending),
                         'pending_document_count':len({c['document_id'] for c in pending.values()})})
        unread=[{k:v for k,v in c.items() if k not in ['terms','title_terms']}|{'first_seen_query':first_seen[c['document_id']],
                 'budget_skip_attempts':skips[c['id']]} for c in pending.values()]
        return rows,unread

def ranges_from_row(row):
    return {d['document_id']:[[s['source_start'],s['source_end']] for s in d['segments']] for d in row['payload']}

def check_plans(rows, queries, docs, selector):
    available=set()
    for row,q in zip(rows,queries):
        available.update(q['result_ids'])
        assert row['tokens']<=row['token_budget']
        assert row['visible_chars']<=row['visible_char_budget']
        assert {d['document_id'] for d in row['payload']}<=available
        for doc in row['payload']:
            spans=[]
            for s in doc['segments']:
                assert docs[doc['document_id']]['text'][s['source_start']:s['source_end']]==s['text']
                spans.append([s['source_start'],s['source_end']])
            assert spans==merge_ranges(spans)
        assert selector.cost(ranges_from_row(row))==(row['tokens'],row['visible_chars'])

def summarize(rows,labels,retention):
    union=defaultdict(list)
    for row in rows:
        for did,spans in ranges_from_row(row).items():union[did].extend(spans)
    def hit(label,row=None):
        ranges=union if row is None else ranges_from_row(row)
        return contains(ranges.get(label['document_id'],[]),label['start'],label['end'])
    hits={l['name']:[r['query_number'] for r in rows if hit(l,r)] for l in labels}
    event_dates=sorted({l['gold_date'] for l in labels if l['kind']=='event' and hits[l['name']]})
    retained=[l['candidate_id'] for l in retention if hit(l)]
    simultaneous=[l['candidate_id'] for l in retention if any(hit(l,row) for row in rows)]
    return {'queries':len(rows),'evidence_payload_tokens':sum(r['tokens'] for r in rows),
            'visible_chars':sum(r['visible_chars'] for r in rows),'documents':len(union),
            'segment_occurrences':sum(len(d['segments']) for r in rows for d in r['payload']),
            'event_evidence_dates':event_dates,
            'relative_date_evidence_dates':sorted({l['gold_date'] for l in labels if l['kind']=='relative_date' and hits[l['name']] and l['gold_date'] in event_dates}),
            'preview_hits':[l['name'] for l in labels if l['kind']=='preview' and hits[l['name']]],
            'label_hits':hits,'retained_quote_ids_across_run':retained,
            'retained_quote_ids_same_query':simultaneous,'retention_denominator':len(retention),
            'retention_same_query':len(simultaneous),
            'choices_from_past_documents':sum(c['from_previous_query'] for r in rows for c in r.get('choices',[])),
            'choices_outside_current_top12':sum(c['outside_current_top12'] for r in rows for c in r.get('choices',[]))}

def self_checks(tokenizer):
    assert merge_ranges([[4,8],[0,4],[12,15]])==[[0,8],[12,15]]
    assert not contains([[0,4],[5,9]],0,9), 'Never bridge a missing source character'
    assert contains([[0,4],[4,9]],0,9)
    doc={'id':'a','title':'David Beckham','publication_date':'2008-03-27',
         'text':'David Beckham made his 100th appearance last night.\nHe was substituted after an hour.'}
    cs=sentence_candidates(doc)
    assert cs[0]['has_date'] and cs[1]['start']==cs[0]['start']
    selector=Selector({'a':doc},{'a':cs},tokenizer)
    payload=selector.payload({'a':[[0,4],[7,12]]})
    assert len(payload[0]['segments'])==2
    assert selector.cost({'a':[[0,4],[7,12]]})[1]==9
    return {'range_union_without_gap_stitching':True,'date_cue_and_pronoun_context':True,
            'separate_segment_serialization':True}

def run():
    audit_path=ROOT/'artifacts/beckham_original_phase1_audit/report.json'
    prior_path=ROOT/'artifacts/beckham_passage_selection_offline/report.json'
    plans_path=ROOT/'artifacts/beckham_passage_selection_offline/selection_plans.json'
    trace_path=ROOT/'artifacts/tisa_v97_authorized/entities_David_Beckham/trajectory.json'
    hashes={str(p):sha(p) for p in [audit_path,prior_path,plans_path,trace_path]}
    audit=json.loads(audit_path.read_text(encoding='utf-8'))
    assert hashes[str(trace_path)]==audit['source_sha256']
    # No labels or historical candidate outputs are passed to the selector.
    prior_plans=json.loads(plans_path.read_text(encoding='utf-8'))
    queries=[copy.deepcopy(q) for b in audit['batches'] for q in b['queries']]
    for q,row in zip(queries,prior_plans['recorded']):q['baseline']=row['passages']
    assert len(queries)==51
    index=ROOT/'artifacts/entities_25ac73e52bc93b3b.sqlite3'
    con=sqlite3.connect(index.as_uri()+'?mode=ro',uri=True)
    docs={str(i):{'id':str(i),'title':title,'text':text,'publication_date':date}
          for i,title,text,date in con.execute('SELECT doc_id,title,text,timestamp FROM documents WHERE topic=?',('David_Beckham',))}
    con.close()
    needed={i for q in queries for i in q['result_ids']}
    docs={i:d for i,d in docs.items() if i in needed}
    candidates={i:sentence_candidates(d) for i,d in docs.items()}
    tokenizer_path=ROOT/'artifacts/deepseek_v4_tokenizer/tokenizer.json'
    tokenizer=Tokenizer.from_file(str(tokenizer_path))
    checks=self_checks(tokenizer)
    selector=Selector(docs,candidates,tokenizer)
    OUT.mkdir(exist_ok=True)
    (OUT/'policy.json').write_text(json.dumps(POLICY,ensure_ascii=False,indent=2),encoding='utf-8')
    plans={};queues={}
    for name in ['recorded','same_documents_leads','top12_lead_plus_original']:
        rows=[]
        for n,(old,q) in enumerate(zip(prior_plans[name],queries),1):
            ranges=visible_ranges(old['passages']);budget=selector.cost(visible_ranges(q['baseline']));cost=selector.cost(ranges)
            rows.append({'query_number':n,'query':q['query'],'current_result_ids':q['result_ids'],
                         'token_budget':budget[0],'visible_char_budget':budget[1],
                         'tokens':cost[0],'visible_chars':cost[1],'payload':selector.payload(ranges)})
        plans[name]=rows
    for name,persistent in [('sentence_union_no_queue',False),('sentence_union_with_queue',True)]:
        print('Selecting',name,flush=True)
        plans[name],queues[name]=selector.run(queries,persistent)
        check_plans(plans[name],queries,docs,selector)
    # Evaluate only after both policy arms are fixed and executed.
    prior=json.loads(prior_path.read_text(encoding='utf-8'))
    labels=prior['diagnostic_labels']
    for l in labels:assert docs[l['document_id']]['text'][l['start']:l['end']]==l['quote']
    retention=[]
    for c in audit['candidates']:
        if c['status']!='SUPPORTED':continue
        e=c['date_evidence'];did=e['document_id'].split('@')[0];quote=e['quote']
        start=docs[did]['text'].index(quote)
        retention.append({'candidate_id':c['candidate_id'],'document_id':did,'start':start,'end':start+len(quote),'quote':quote})
    results={name:summarize(rows,labels,retention) for name,rows in plans.items()}
    checks.update({'query_count':len(queries)==51,'new_arm_per_query_budget_checks':True,
                   'source_offsets_and_no_future_documents':True,
                   'historical_sources_unchanged':all(sha(Path(p))==h for p,h in hashes.items())})
    assert all(checks.values())
    baseline_ranges=defaultdict(list)
    for r in plans['recorded']:
        for did,spans in ranges_from_row(r).items():baseline_ranges[did].extend(spans)
    for name,rows in plans.items():
        results[name]['queries_over_normalized_budget']=sum(r['tokens']>r['token_budget'] for r in rows)
    report={'observed_at_utc':datetime.now(timezone.utc).isoformat(),'api_calls':0,
            'scope':'Original v9.7 first phase: 51 fixed queries, 209 returned documents; no supplement or second phase.',
            'source_hashes':hashes,'script_sha256':sha(Path(__file__)),'tokenizer_sha256':sha(tokenizer_path),
            'policy':POLICY,'checks':checks,'results':results,'diagnostic_labels':labels,
            'queue_final_counts':{k:len(v) for k,v in queues.items()},
            'limitations':['Main-event candidates are opening-sentence heuristics, not LLM-verified main events.',
                'Date cues are not normalized dates or proof of correct event-date relation.',
                'Source-line segmentation can split or group linguistic sentences imperfectly.',
                'Fixed historical queries are counterfactual after changed evidence; no adaptive rollout.',
                'Nine existing labels cover three event groups and two previews; not exhaustive Gold qrels or a held-out test.',
                'Quote retention concerns historical SUPPORTED candidates including duplicates and date errors.',
                'Both queue arms avoid already-read spans. Only eligibility of unselected past candidates differs.',
                'Tokens include common evidence bundle metadata and boundaries, but not complete VERIFY system/state prompts.',
                'Previous strategies are reserialized in the new common schema; token totals are not the old projection totals.',
                'No API, extraction, MERGE, source-date correction, production deployment or training readiness assessment.']}
    for name,value in [('report.json',report),('selection_plans.json',plans),('unread_queues.json',queues)]:
        (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,res in results.items():
        print(name,json.dumps({k:v for k,v in res.items() if not k.startswith('retained_quote_ids') and k!='label_hits'},ensure_ascii=False),flush=True)
    print('LABELS',json.dumps({name:r['label_hits'] for name,r in results.items()},ensure_ascii=False),flush=True)

if __name__=='__main__':run()
