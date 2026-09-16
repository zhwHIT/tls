"""Offline benchmark precision audit, never used by a deployment controller."""
from collections import Counter
import json
from pathlib import Path
import re

from chronos_repro.data import _read_jsonish, parse_date

root = Path(__file__).resolve().parents[1]
suite = json.loads((root / 'configs/tisa_multitopic_v7_diagnostic.json').read_text(encoding='utf-8'))
reports = []
for item in suite['topics']:
    config = json.loads((root / item['config']).read_text(encoding='utf-8'))
    path = root / config['data'] / config['topic'] / 'timelines.jsonl'
    precisions, canonical, partial = Counter(), set(), set()
    for index, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        for raw, events in _read_jsonish(line, path, index):
            raw = str(raw)
            precision = 'day' if re.match(r'^\d{4}-\d{2}-\d{2}', raw) else 'month' if re.fullmatch(r'\d{4}-\d{2}', raw) else 'year' if re.fullmatch(r'\d{4}', raw) else 'other'
            precisions[precision] += 1
            value = parse_date(raw).isoformat()
            canonical.add(value)
            if precision in {'month', 'year'}:
                partial.add(value)
    reports.append({'dataset': item['dataset'], 'topic': item['topic'], 'raw_reference_date_precision': dict(precisions),
                    'canonical_gold_date_count': len(canonical), 'partial_dates_mapped_to_first_day': sorted(partial)})
report = {'topics': reports, 'api_calls': 0}
print(json.dumps(report, ensure_ascii=False, indent=2))
with (root / 'artifacts/diagnostic_gold_precision_profile.json').open('x', encoding='utf-8') as target:
    json.dump(report, target, ensure_ascii=False, indent=2)
