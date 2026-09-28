"""Offline factual comparison; never feeds Gold to the running generator."""
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'artifacts/tisa_v10_lowest_phase1_api'
OUT = RUN / 'supplement_comparison'
OUT.mkdir(exist_ok=True)
source = RUN / 'entities_David_Beckham/checkpoint.json'
raw = source.read_bytes()
(OUT / 'source_checkpoint.json').write_bytes(raw)
s = json.loads(raw)
b = json.loads((RUN / 'David_Beckham_phase1_baseline.json').read_text(encoding='utf-8'))
gold = json.loads((ROOT / 'snapshots/entities/25ac73e52bc93b3b/David_Beckham/timelines.jsonl').read_text(encoding='utf-8'))
gold = [{'date': d[:10], 'summaries': text} for d, text in gold]
before = {e['event_id']: e for e in b['events']}
events = copy.deepcopy(before)
operations = []
for st in s['steps']:
    if st['action'] == 'MERGE':
        candidates = {c['candidate_id']: c for c in st['model_input'].get('tool_observation', {}).get('verified_candidates', [])}
        obs = st.get('observation', {})
        for op in obs.get('applied', []):
            operations.append({'phase': st['phase'], **op})
            if op['applied_operation'] == 'APPEND':
                c = candidates[op['candidate_id']]
                events[op['event_id']] = {**copy.deepcopy(c['event']), 'event_id': op['event_id'], 'date_evidence': c.get('date_evidence'), 'evidence_ids': c['evidence_ids']}
        for f in obs.get('update_fusions', []):
            events[f['merged_event']['event_id']] = copy.deepcopy(f['merged_event'])
    if st['phase'] == 'PHASE1_SUPPLEMENT' and st['action'] == 'COMPLETE':
        assert len(events) == st['model_output']['event_count']
        break
else:
    raise ValueError('Supplement not complete')
new = [e for key, e in events.items() if key not in before]
intervals = s['phase1_supplement']['intervals']
for g in gold:
    g['selected_intervals'] = [i['interval_id'] for i in intervals if i['start'] <= g['date'] <= i['end']]
counts = Counter(o['applied_operation'] for o in operations if o['phase'] == 'PHASE1_SUPPLEMENT')
report = {'source_sha256': hashlib.sha256(raw).hexdigest(), 'baseline_count': len(before), 'supplement_count': len(events),
          'new_count': len(new), 'baseline_dates_unchanged': all(events[k]['time'] == e['time'] for k, e in before.items()),
          'supplement_operations': dict(counts), 'nonappend_operations': [o for o in operations if o['applied_operation'] != 'APPEND'],
          'intervals': intervals, 'gold': gold, 'gold_dates_inside_selected_intervals': sum(bool(g['selected_intervals']) for g in gold),
          'before_date_hits': sum(g['date'] in {e['time'] for e in before.values()} for g in gold),
          'after_date_hits': sum(g['date'] in {e['time'] for e in events.values()} for g in gold),
          'new_events': new, 'baseline_events': list(before.values()), 'supplement_events': list(events.values()),
          'year_counts_before': dict(sorted(Counter(e['time'][:4] for e in before.values()).items())),
          'year_counts_added': dict(sorted(Counter(e['time'][:4] for e in new).items()))}
(OUT / 'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
lines = ['# Beckham 补充前后事件差异', '', '仅重放补充阶段结束前的操作；不混入正在运行的第二阶段。', '',
         f"基线 {len(before)} 条；补充后 {len(events)} 条；新增 {len(new)} 条。Gold 日期命中 {report['before_date_hits']}/16 → {report['after_date_hits']}/16。", '',
         '## 新增事件（原始模型摘要，未经人工全面验真）', '', '| ID | 日期 | 摘要 |', '|---|---|---|']
lines += [f"| {e['event_id']} | {e['time']} | {e['summary'].replace('|', '/')} |" for e in sorted(new, key=lambda e:(e['time'],e['event_id']))]
lines += ['', '## Gold 遗漏目标', '', '| 日期 | Gold 摘要 | 是否在选中区间 |', '|---|---|---|']
lines += [f"| {g['date']} | {' '.join(g['summaries'])} | {bool(g['selected_intervals'])} |" for g in gold]
(OUT / 'comparison.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ['new_events','baseline_events','supplement_events','gold','intervals']}, ensure_ascii=True))
