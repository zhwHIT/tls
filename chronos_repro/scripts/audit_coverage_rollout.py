"""Read-only v6 evidence provenance and VERIFY audit (not a semantic quality judge)."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from chronos_repro.full_timeline import validate_verified_candidates
from chronos_repro.retrieval import _bm25_path


def audit_trace(trace, manifest, originals):
    passages = {p['id']: p for p in manifest['passages']}
    failures, checks = [], 0
    retrieved_docs = set()
    approved = set(trace.get('approved_passage_ids', []))
    for p in passages.values():
        original = originals.get(p['document_id'])
        if original is None:
            failures.append({'passage_id': p['id'], 'error': 'source document missing'})
            continue
        text = original['text']
        expected = text[p['source_start']:p['source_end']]
        if (expected != p['text'] or hashlib.sha256(text.encode()).hexdigest() != p['document_sha256']
                or text[max(0, p['source_start'] - 400):p['source_start']] != p.get('context_before', '')
                or str(original.get('title') or '') != p['title']):
            failures.append({'passage_id': p['id'], 'error': 'source offset, title, context or document hash mismatch'})
    for step in trace['steps']:
        if step['action'] == 'SEARCH':
            retrieved_docs.update(str(r['id']) for r in step.get('observation', {}).get('results', []))
        if step['action'] != 'VERIFY':
            continue
        docs = step.get('model_input', {}).get('tool_observation', {}).get('retrieved_documents', [])
        if not docs:  # Explicit empty-query transition.
            continue
        try:
            for d in docs:
                canonical = passages.get(d['id'])
                if canonical is None or any(d.get(k) != canonical.get(k) for k in ('text', 'title', 'context_before')):
                    raise ValueError('Model did not receive the recorded source passage')
                if d['id'] not in approved and canonical['document_id'] not in retrieved_docs:
                    raise ValueError('Evidence parent document was never retrieved')
            rows = step['model_output']['candidates']
            validate_verified_candidates(rows, docs, len(rows), strict_dates=True)
            checks += 1
        except (KeyError, ValueError, TypeError) as error:
            failures.append({'step_id': step.get('step_id'), 'error': str(error)})
    return {'valid': not failures and checks > 0, 'verified_batches_checked': checks,
            'source_passages_checked': len(passages), 'failures': failures,
            'limitations': 'Evidence provenance and date-expression checks only; not event entailment, coverage or STOP correctness.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--index', required=True)
    parser.add_argument('--topic', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    folder = Path(args.run_dir)
    trace = json.loads((folder / 'trajectory.json').read_text(encoding='utf-8'))
    manifest = json.loads((folder / 'evidence_reader_manifest.json').read_text(encoding='utf-8'))
    ids = sorted({p['document_id'] for p in manifest['passages']})
    originals = {}
    with sqlite3.connect(_bm25_path(args.index).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            placeholders = ','.join('?' for _ in batch)
            rows = connection.execute(f'SELECT doc_id,title,text FROM documents WHERE topic=? AND doc_id IN ({placeholders})',
                                      [args.topic, *batch]).fetchall()
            originals.update({str(i): {'title': title, 'text': text} for i, title, text in rows})
    report = audit_trace(trace, manifest, originals)
    target = Path(args.output)
    # Avoid replacing input artifacts with a report.
    if target.resolve() in {(folder / n).resolve() for n in ('trajectory.json', 'evidence_reader_manifest.json', 'prediction.json')}:
        raise ValueError('Audit output must not overwrite an input artifact')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    if not report['valid']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
