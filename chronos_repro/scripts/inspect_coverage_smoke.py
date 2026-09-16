"""Offline diagnostics for cached smoke responses; never calls an API."""
import argparse
import json
from collections import Counter
from pathlib import Path

from chronos_repro.full_timeline import validate_verified_candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--preview', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    folder = Path(args.run_dir)
    docs = json.loads(Path(args.preview).read_text(encoding='utf-8'))['passages']
    records = []
    for path in sorted((folder / 'api_cache').glob('*.json'), key=lambda p: p.stat().st_mtime):
        response = json.loads(path.read_text(encoding='utf-8'))['response']
        row = {'cache_file': path.name, 'usage': response.get('usage', {}), 'attempts': response.get('attempts')}
        try:
            payload = json.loads(response['text'])
            row['thought_length'] = len(payload.get('thought', ''))
            row['kind'] = 'VERIFY' if 'candidates' in payload else 'MERGE' if 'operations' in payload else 'OTHER'
            if 'candidates' in payload:
                row['candidate_count'] = len(payload['candidates'])
                row['statuses'] = dict(Counter(c['status'] for c in payload['candidates']))
                row['date_errors'] = []
                for candidate in payload['candidates']:
                    try:
                        validate_verified_candidates([candidate], docs, 1, strict_dates=True)
                    except (ValueError, TypeError, KeyError) as error:
                        row['date_errors'].append({'candidate_id': candidate.get('candidate_id'), 'error': str(error)})
        except (ValueError, TypeError, KeyError) as error:
            row['parse_error'] = str(error)
        records.append(row)
    report = {'scope': 'Offline cached-response diagnostics, not an independent semantic audit', 'responses': records}
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
