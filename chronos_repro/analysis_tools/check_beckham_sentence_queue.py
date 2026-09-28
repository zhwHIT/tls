"""Independent result accounting and small behavioral checks for the offline experiment."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from tokenizers import Tokenizer
from experiment_beckham_sentence_queue import OUT, ROOT, Selector, sentence_candidates

def main():
    report=json.loads((OUT/'report.json').read_text(encoding='utf-8'))
    plans=json.loads((OUT/'selection_plans.json').read_text(encoding='utf-8'))
    for path,h in report['source_hashes'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==h
    tokenizer=Tokenizer.from_file(str(ROOT/'artifacts/deepseek_v4_tokenizer/tokenizer.json'))
    for name,rows in plans.items():
        assert len(rows)==51
        summed=0
        for row in rows:
            wire=json.dumps(row['payload'],ensure_ascii=False,separators=(',',':'))
            actual=len(tokenizer.encode(wire,add_special_tokens=False).ids)
            assert actual==row['tokens']
            if name.startswith('sentence_union'):
                assert actual<=row['token_budget']
                assert sum(len(s['text']) for d in row['payload'] for s in d['segments'])<=row['visible_char_budget']
            summed+=actual
        assert summed==report['results'][name]['evidence_payload_tokens']
    def setup(second_text):
        docs={i:{'id':i,'title':'David Beckham','publication_date':'2008-03-27','text':text}
              for i,text in [('a','David Beckham appeared yesterday in London.'),('b',second_text)]}
        base={'document_id':'a','source_start':0,'source_end':len(docs['a']['text']),'context_before':''}
        queries=[{'query':'David Beckham appeared','result_ids':['a','b'],'baseline':[base]},
                 {'query':'David Beckham appeared','result_ids':['a'],'baseline':[base]}]
        selector=Selector(docs,{i:sentence_candidates(d) for i,d in docs.items()},tokenizer)
        return selector,queries
    selector,queries=setup('David Beckham left yesterday.')
    no_queue,_=selector.run(queries,False)
    queue,_=selector.run(queries,True)
    assert [d['document_id'] for d in no_queue[0]['payload']]==['a']
    assert no_queue[1]['payload']==[]
    assert [d['document_id'] for d in queue[1]['payload']]==['b']
    selector,queries=setup('David Beckham yesterday '+('evidence '*300)+'.')
    rows,pending=selector.run(queries[:1],True)
    assert all(d['document_id']!='b' for d in rows[0]['payload'])
    assert any(c['document_id']=='b' and c['budget_skip_attempts']>0 for c in pending)
    joint={}
    for name,summary in report['results'].items():
        joint[name]={}
        for date in sorted({l['gold_date'] for l in report['diagnostic_labels'] if l['kind']=='relative_date'}):
            event_q={q for l in report['diagnostic_labels'] if l['kind']=='event' and l['gold_date']==date
                     for q in summary['label_hits'][l['name']]}
            date_q={q for l in report['diagnostic_labels'] if l['kind']=='relative_date' and l['gold_date']==date
                    for q in summary['label_hits'][l['name']]}
            joint[name][date]=sorted(event_q & date_q)
    queue_only=[]
    for row,baseline in zip(plans['sentence_union_with_queue'],plans['recorded']):
        original_docs={d['document_id'] for d in baseline['payload']}
        for choice in row['choices']:
            did=choice['candidate_id'].split(':')[0]
            if choice['outside_current_top12'] and did not in original_docs:
                queue_only.append({'query_number':row['query_number'],**choice})
    result={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'api_calls':0,
            'checks':{'source_hashes_unchanged':True,'independent_token_accounting_all_five_arms':True,
                      'new_arms_per_query_budget':True,'candidate_survives_query_expiration':True,
                      'oversized_sentence_not_truncated_or_marked_read':True},
            'source_report_sha256':hashlib.sha256((OUT/'report.json').read_bytes()).hexdigest(),
            'same_query_event_and_date_evidence':joint,
            'queue_only_choice_count':len(queue_only),'queue_only_choice_examples':queue_only[:10],
            'selection_window_seconds':round((OUT/'report.json').stat().st_mtime-(OUT/'policy.json').stat().st_mtime,3),
            'timing_note':'File timestamp interval from policy write to report write, not per-arm benchmark or API latency.'}
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
