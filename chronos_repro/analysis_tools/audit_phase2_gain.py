"""Audit saved phase-two additions and missing Gold dates without API calls."""
import hashlib
import json
from bisect import bisect_left
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/phase1_span_audit_20260917/report.json'
OUT = ROOT / 'artifacts/phase2_gain_audit_20260917'
OUT.mkdir(exist_ok=True)
source_hashes = {}
def read(p):
    p = Path(p).resolve()
    raw = p.read_bytes()
    source_hashes[str(p)] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)

def chain(p):
    d = read(p)
    c = d.get('continuation')
    before = chain(c['parent_snapshot']) if c else []
    if c:
        assert source_hashes[str(Path(c['parent_snapshot']).resolve())] == c['parent_snapshot_sha256']
    return before + [(str(p), d)]

base = read(BASE)
rows = []
for old in base['rows']:
    lineage = chain(old['latest_path'])
    d = lineage[-1][1]
    assert d['status'] == 'ok'
    original = old['phase1_event_dates']
    final = {e['event_id']:e['time'] for e in d['final_events']}
    gold = set(old['gold_dates'])
    first_dates, final_dates = set(original.values()), set(final.values())
    new_ids = set(final)-set(original)
    new_dates = {final[k] for k in new_ids}
    append_ids, update_ids = [], []
    gap_creation = {}
    known = dict(original)
    modes = Counter()
    for path, trace in lineage:
        for s in trace['steps']:
            inp = s.get('model_input', {})
            if inp.get('stage') == 'GAP_DISCOVERY':
                for e in inp.get('events', []):
                    known[e['event_id']] = e['time']
            if s['phase'] == 'SKELETON_EXPLORATION':
                continue
            if s['action'] == 'SEARCH':
                for q in s.get('model_output', {}).get('queries', []):
                    modes[q.get('time_filter', {}).get('mode', 'missing')] += 1
            if s['action'] == 'MERGE':
                candidates = {c['candidate_id']:c for c in inp.get('tool_observation', {}).get('verified_candidates', [])}
                obs = s.get('observation', {})
                for op in obs.get('applied', []):
                    if op['applied_operation'] == 'APPEND':
                        append_ids.append(op['event_id'])
                        known[op['event_id']] = candidates[op['candidate_id']]['event']['time']
                    elif op['applied_operation'] == 'UPDATE':
                        update_ids.append(op['event_id'])
                for f in obs.get('update_fusions', []):
                    e = f['merged_event']; known[e['event_id']] = e['time']
    known.update(final)
    assert len(append_ids) == len(set(append_ids)), old['topic']
    assert new_ids <= set(append_ids), old['topic']
    missing = gold-final_dates
    p1miss = gold-first_dates
    gaps = d.get('final_gap_memory', {}).get('gaps', [])
    intervals = []
    unbounded = []
    single = 0
    for g in gaps:
        left,right = g.get('left_event_id'),g.get('right_event_id')
        if not left or not right:
            single += 1
            continue
        if left not in known or right not in known:
            unbounded.append(g['gap_id']); continue
        lo,hi = sorted([known[left],known[right]])
        intervals.append({'gap_id':g['gap_id'],'status':g['status'],'start':lo,'end':hi,
                          'question':g.get('retrieval_target',{}).get('question',g.get('description')),
                          'left_event_id':left,'right_event_id':right,
                          'anchor_dates_from':'latest_saved_event_dates_in_lineage_not_a_declared_gap_time_filter'})
    explicit_matches = {x:[g for g in intervals if g['start']<=x<=g['end']] for x in sorted(missing)}
    before = sorted(x for x in missing if x<min(final_dates))
    after = sorted(x for x in missing if x>max(final_dates))
    inside = sorted(missing-set(before)-set(after))
    date_order = sorted(final_dates)
    buckets = Counter()
    for x in inside:
        at = bisect_left(date_order,x)
        assert 0<at<len(date_order) and date_order[at]!=x
        buckets[date_order[at-1],date_order[at]] += 1
    row = {'dataset':old['dataset'],'topic':old['topic'],'gold_count':len(gold),
           'phase1_events':len(original),'phase2_append_operations':len(append_ids),
           'phase2_update_operations':len(update_ids),'phase2_updated_original_ids':len(set(update_ids)&set(original)),
           'final_events':len(final),'retained_new_events':len(new_ids),
           'removed_original_events':len(set(original)-set(final)),
           'removed_new_events':len(set(append_ids)-set(final)),
           'net_event_gain':len(final)-len(original),
           'new_event_gold_date_hits':len(new_dates&gold),'new_event_unique_dates':len(new_dates),
           'new_event_date_recall':len(new_dates&gold)/len(gold),
           'new_event_novel_gold_dates':len((new_dates&gold)-first_dates),
           'new_event_repeat_gold_dates':len(new_dates&gold&first_dates),
           'phase1_gold_hits':len(first_dates&gold),'final_gold_hits':len(final_dates&gold),
           'all_phase2_recovered_dates':len(p1miss&final_dates),
           'lost_phase1_gold_dates':sorted((first_dates&gold)-final_dates),
           'phase1_missing_count':len(p1miss),'final_missing_count':len(missing),
           'remaining_miss_before_final_span':before,'remaining_miss_inside_final_span':inside,
           'remaining_miss_after_final_span':after,'remaining_miss_by_year':dict(sorted(Counter(x[:4] for x in missing).items())),
           'remaining_miss_before_phase1_span':sorted(x for x in missing if x<min(first_dates)),
           'remaining_miss_after_phase1_span':sorted(x for x in missing if x>max(first_dates)),
           'largest_calendar_holes':[{'start':lo,'end':hi,'missing_gold_dates':n} for (lo,hi),n in buckets.most_common(5)],
           'gap_count':len(gaps),'gap_statuses':dict(Counter(g['status'] for g in gaps)),
           'single_anchor_gaps':single,'two_anchor_gaps':len(intervals),'unresolved_anchor_ids':unbounded,
           'two_anchor_intervals':intervals,'missing_in_two_anchor_envelopes':sum(bool(v) for v in explicit_matches.values()),
           'missing_two_anchor_matches':{x:[g['gap_id'] for g in v] for x,v in explicit_matches.items() if v},
           'phase2_query_time_filter_modes':dict(modes),'retained_new_event_ids':sorted(new_ids),
           'new_event_gold_dates':sorted(new_dates&gold),'new_event_novel_gold_date_list':sorted((new_dates&gold)-first_dates),
           'final_missing_dates':sorted(missing),'latest_path':old['latest_path']}
    assert row['net_event_gain']==row['retained_new_events']-row['removed_original_events']
    assert row['final_gold_hits']==row['phase1_gold_hits']+row['all_phase2_recovered_dates']-len(row['lost_phase1_gold_dates'])
    rows.append(row)

SUM = ['gold_count','phase1_events','phase2_append_operations','phase2_update_operations','phase2_updated_original_ids','final_events','retained_new_events','removed_original_events','removed_new_events','net_event_gain','new_event_gold_date_hits','new_event_unique_dates','new_event_novel_gold_dates','new_event_repeat_gold_dates','phase1_gold_hits','final_gold_hits','all_phase2_recovered_dates','phase1_missing_count','final_missing_count','gap_count','single_anchor_gaps','two_anchor_gaps','missing_in_two_anchor_envelopes']
def aggregate(group):
    a = {k:sum(r[k] for r in group) for k in SUM}
    a.update(topics=len(group),new_date_micro=a['new_event_gold_date_hits']/a['gold_count'],
             new_date_macro=sum(r['new_event_date_recall'] for r in group)/len(group),
             new_date_precision=a['new_event_gold_date_hits']/a['new_event_unique_dates'],
             novel_date_share_all_gold=a['new_event_novel_gold_dates']/a['gold_count'],
             new_recovery_of_phase1_missing=a['new_event_novel_gold_dates']/a['phase1_missing_count'])
    for k in ['remaining_miss_before_final_span','remaining_miss_inside_final_span','remaining_miss_after_final_span','lost_phase1_gold_dates']:
        a[k]=sum(len(r[k]) for r in group)
    for k in ['gap_statuses','phase2_query_time_filter_modes']:
        c=Counter()
        for r in group:c.update(r[k])
        a[k]=dict(c)
    return a

report={'observed_at':datetime.now().astimezone().isoformat(),'api_calls':0,
        'definitions':{'new_events':'Final retained event IDs absent at phase-one boundary; updates to old IDs are excluded',
                       'coverage':'Union of exact Gold dates per topic, not semantic event coverage',
                       'gap_interval':'Diagnostic envelope between two saved anchor dates only; gap schema does not declare start/end; single-anchor gaps cannot be treated as bounded intervals'},
        'limitations':['Mixed framework versions and continuations; not a controlled quality comparison','Legacy input integrity and date correctness remain unvalidated','Temporal overlap with a gap does not establish that its question covers a Gold event'],
        'datasets':{ds:aggregate([r for r in rows if r['dataset']==ds]) for ds in sorted({r['dataset'] for r in rows})},
        'all':aggregate(rows),'rows':rows,'source_hashes':source_hashes}
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in source_hashes.items())
with (OUT/'report.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
print(json.dumps({'datasets':report['datasets'],'all':report['all']},indent=2))
print('TOP MISSING',[(r['dataset'],r['topic'],r['final_missing_count'],r['missing_in_two_anchor_envelopes']) for r in sorted(rows,key=lambda r:-r['final_missing_count'])[:10]])
