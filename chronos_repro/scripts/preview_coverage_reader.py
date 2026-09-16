"""Prepare a bounded, auditable passage preview from historical retrieved IDs. No API."""
import argparse
import json
from pathlib import Path

from chronos_repro.evidence_access import EvidenceReader
from chronos_repro.date_evidence import explicit_dates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--historical-run', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    historical = Path(args.historical_run).resolve()
    output = Path(args.output).resolve()
    if historical in output.parents:
        raise ValueError('Preview must not overwrite the historical run')
    trace = json.loads((historical / 'trajectory.json').read_text(encoding='utf-8'))
    first = next(s for s in trace['steps'] if s['action'] == 'SEARCH')
    old_docs = first['observation']['documents']
    reader = EvidenceReader(config['index'], config['topic'], config['coverage_pipeline'])
    reader.add_results([{'id': d['id']} for d in old_docs])
    query = first['model_output']['query']
    passages = reader.select(query)
    def days(docs):
        return sorted({v for d in docs for v in explicit_dates(d['text']) if len(v) == 10})
    report = {'new_api_calls': 0, 'query': query, 'same_retrieved_document_ids': [d['id'] for d in old_docs],
              'old_characters': sum(len(d['text']) for d in old_docs),
              'new_characters': sum(len(d['text']) for d in passages),
              'old_explicit_date_mentions': days(old_docs), 'new_explicit_date_mentions': days(passages),
              'reader': reader.summary(), 'passages': passages,
              'limitations': 'Date mentions are exposure diagnostics, not verified events or Gold recall; input lengths differ.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'passages'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
