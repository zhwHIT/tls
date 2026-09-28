"""Read-only date replay of saved phase-one operations; no model calls."""
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from chronos_repro.data import load_timelines

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / 'artifacts'
OUT = ART / 'phase1_span_audit_20260917'
OUT.mkdir(exist_ok=True)
sources = {}
cache = {}

def read(path):
    path = path.resolve()
    raw = path.read_bytes()
    sources[str(path)] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)

def replay(path):
    path = path.resolve()
    if path in cache:
        return copy.deepcopy(cache[path])
    d = read(path)
    lineage = d.get('continuation')
    events = {}
    boundary = None
    origin = None
    if lineage:
        parent = Path(lineage['parent_snapshot'])
        if not parent.resolve().is_relative_to(ROOT):
            raise ValueError('Parent outside project')
        parent_data = replay(parent)
        if sources[str(parent.resolve())] != lineage['parent_snapshot_sha256']:
            raise ValueError('Parent hash changed: ' + str(parent))
        events, boundary, origin = copy.deepcopy(parent_data)
    else:
        events = {e['event_id']: e['time'] for e in d.get('initial_state', {}).get('events', [])}
    for s in d['steps']:
        if s['phase'] != 'SKELETON_EXPLORATION' and boundary is None:
            boundary, origin = copy.deepcopy(events), str(path)
        observation = s.get('observation', {})
        if s['action'] == 'MERGE':
            candidates = {c['candidate_id']: c for c in s['model_input'].get('tool_observation', {}).get('verified_candidates', [])}
            fusions = {f['merged_event']['event_id']: f['merged_event'] for f in observation.get('update_fusions', [])}
            for op in observation.get('applied', []):
                kind = op['applied_operation']
                if kind == 'DROP':
                    continue
                event_id = op['event_id']
                if kind == 'APPEND':
                    assert event_id not in events, (path, s['step_id'], event_id)
                    events[event_id] = candidates[op['candidate_id']]['event']['time']
                elif kind == 'UPDATE':
                    assert event_id in events and event_id in fusions, (path, s['step_id'], op)
                    events[event_id] = fusions[event_id]['time']
                else:
                    raise ValueError(kind)
            if 'event_count' in observation:
                assert len(events) == observation['event_count'], (path, s['step_id'], len(events), observation['event_count'])
    # FINAL_SELECT may remove events, but cannot change dates. Verify replay against saved result.
    final = {e['event_id']: e['time'] for e in d['final_events']}
    assert all(events.get(k) == v for k, v in final.items()), ('final mismatch', path)
    if len(events) != len(final):
        selects = [s for s in d['steps'] if s['model_input'].get('stage') == 'FINAL_SELECT']
        kept = {k for s in selects for k in s['model_output'].get('keep_event_ids', [])}
        assert set(final) == kept, ('unexplained removals', path)
    if boundary is None and d.get('phase1_termination'):
        boundary, origin = copy.deepcopy(events), str(path)
    result = (final, boundary, origin)
    cache[path] = copy.deepcopy(result)
    return result

latest = {}
historical_counts = []
for name in ['tisa_v95_continuation','tisa_v97_authorized','tisa_v98_resumed_and_unstarted','tisa_v99_interrupted_resume']:
    for path in sorted((ART / name).glob('*/trajectory.json')):
        d = read(path)
        latest[d['dataset'], d['topic']] = path
    historical_counts.append({'through': name, 'unique_topics': len(latest), 'statuses': dict(Counter(read(p)['status'] for p in latest.values()))})

rows = []
for (dataset, topic), path in sorted(latest.items()):
    d = read(path)
    final, phase1, origin = replay(path)
    if phase1 is None:
        rows.append({'dataset':dataset,'topic':topic,'excluded':'phase_one_not_finished'})
        continue
    config = read(path.parent / 'run_config.json')
    gold_path = ROOT / config['data'] / topic / 'timelines.jsonl'
    sources[str(gold_path.resolve())] = hashlib.sha256(gold_path.read_bytes()).hexdigest()
    gold = {day.isoformat() for timeline in load_timelines(gold_path) for day in timeline}
    dates = set(phase1.values())
    lo, hi = (min(dates), max(dates)) if dates else (None, None)
    inside = {x for x in gold if lo <= x <= hi} if dates else set()
    hits = dates & gold
    row = {'dataset':dataset,'topic':topic,'latest_status':d['status'],'latest_path':str(path),
           'phase1_boundary_source':origin,'phase1_source_sha256':read(Path(origin)).get('source_sha256'),
           'phase1_termination':read(Path(origin)).get('phase1_termination'),
           'phase1_events':len(phase1),'phase1_unique_dates':len(dates),
           'predicted_start':lo,'predicted_end':hi,'gold_start':min(gold),'gold_end':max(gold),
           'gold_count':len(gold),'date_hits':len(hits),'date_recall':len(hits)/len(gold),
           'gold_dates_in_span':len(inside),'span_recall':len(inside)/len(gold),
           'contains_full_gold_span':len(inside)==len(gold),
           'missing_before_span':sorted(x for x in gold if lo and x<lo),
           'missing_after_span':sorted(x for x in gold if hi and x>hi),
           'missing_inside_span':sorted(inside-dates),
           'phase1_event_dates':phase1,'gold_dates':sorted(gold),
           'final_date_hits':len(set(final.values())&gold)}
    assert row['date_hits'] <= row['gold_dates_in_span'] <= row['gold_count']
    rows.append(row)

def aggregate(group):
    n = sum(r['gold_count'] for r in group)
    return {'topics':len(group),'gold_dates':n,'date_hits':sum(r['date_hits'] for r in group),
            'date_micro':sum(r['date_hits'] for r in group)/n,
            'date_macro':sum(r['date_recall'] for r in group)/len(group),
            'span_hits':sum(r['gold_dates_in_span'] for r in group),
            'span_micro':sum(r['gold_dates_in_span'] for r in group)/n,
            'span_macro':sum(r['span_recall'] for r in group)/len(group),
            'full_span_topics':sum(r['contains_full_gold_span'] for r in group),
            'missing_inside':sum(len(r['missing_inside_span']) for r in group),
            'missing_before':sum(len(r['missing_before_span']) for r in group),
            'missing_after':sum(len(r['missing_after_span']) for r in group)}

eligible = [r for r in rows if 'excluded' not in r]
groups = {ds:aggregate([r for r in eligible if r['dataset']==ds]) for ds in sorted({r['dataset'] for r in eligible})}
report = {'observed_at':datetime.now().astimezone().isoformat(),'api_calls':0,
          'scope':'Existing unique topics, mixed versions and continuations; descriptive audit, not controlled benchmark',
          'method':'Replay applied MERGE dates with saved UPDATE fusions through first phase boundary; validate event counts and final date replay; verify parent hashes',
          'limitations':['Date matches do not establish event correctness','Span inclusion is only a temporal envelope, not retrieved evidence coverage','Legacy input immutability warnings remain; replay validates consistency, not model wire input integrity'],
          'historical_counts':historical_counts,'datasets':groups,'descriptive_all':aggregate(eligible),
          'rows':rows,'source_hashes':sources}
dest=OUT/'report.json'
with dest.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2,ensure_ascii=False)
print(json.dumps({'historical_counts':historical_counts,'datasets':groups,'all':report['descriptive_all']},indent=2))
for r in eligible:
    print(r['dataset'],r['topic'],f"{r['date_hits']}/{r['gold_count']}",f"span {r['gold_dates_in_span']}/{r['gold_count']}",r['predicted_start'],r['predicted_end'],r['gold_start'],r['gold_end'])
