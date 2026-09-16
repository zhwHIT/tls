"""Verify real retrieved passage annotations locally before any API rollout."""
import json
from pathlib import Path

from chronos_repro.date_evidence import validate_date_evidence
from chronos_repro.frozen_timex import load_topic_annotations, attach_annotations

root = Path(__file__).resolve().parents[1]
trace = json.loads((root / 'artifacts/tisa_v7_diagnostic/crisis_yemen/history/20260914T130120-a6182b77/trajectory.json').read_text(encoding='utf-8'))
passages = {d['id']: d for s in trace['steps'] if s['action'] == 'SEARCH' for d in s['observation'].get('documents', [])}
annotations = load_topic_annotations(str(root / 'snapshots/crisis/ece08f344cc94933/yemen/articles.preprocessed.jsonl.gz'))
valid, errors = 0, []
for passage in passages.values():
    attach_annotations(passage, annotations.get(passage['document_id'], []))
    for row in passage['temporal_annotations']:
        a, b = row['source_start'] - passage['source_start'], row['source_end'] - passage['source_start']
        evidence = {'document_id': passage['id'], 'annotation_id': row['annotation_id'], 'time_expression': row['expression'],
                    'quote': passage['text'][max(0, a - 80):min(len(passage['text']), b + 100)]}
        try:
            validate_date_evidence(row['date'], evidence, [passage], [passage['id']])
            valid += 1
        except ValueError as error:
            errors.append({'annotation': row, 'error': str(error)})
report = {'passages': len(passages), 'validated_annotations': valid, 'errors': errors, 'api_calls': 0,
          'note': 'Only temporal provenance contract tested, not semantic event correctness'}
print(json.dumps(report, ensure_ascii=False, indent=2))
with (root / 'artifacts/yemen_frozen_timex_contract.json').open('x', encoding='utf-8') as target:
    json.dump(report, target, ensure_ascii=False, indent=2)
