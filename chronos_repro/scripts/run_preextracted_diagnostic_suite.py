"""Run three authorized first-phase diagnostics concurrently and compare dates."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from chronos_repro.atomic_io import atomic_write_json
from chronos_repro.run_guard import exclusive_run, source_binding
from chronos_repro.snapshot import sha256


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-api', action='store_true')
    parser.add_argument('--suite-config', default='configs/preextracted_phase1_diagnostic_suite.json')
    parser.add_argument('--output-root', default='artifacts/preextracted_phase1_20260920')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    suite = json.loads((project / args.suite_config).read_text(encoding='utf-8'))
    root = project / args.output_root
    if not args.allow_api:
        print(json.dumps({'api_calls': 0, 'plan': suite}, ensure_ascii=False))
        return 0
    with exclusive_run(root):
        binding = source_binding(project)
        state = {'stage': 'running', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'parallel_topics': 3, 'completed': [], 'plan': suite['topics'],
                 'source_sha256': binding['source_sha256']}
        atomic_write_json(root / 'suite_status.json', state)

        def run(item):
            output = project / item['output_dir']
            output.mkdir(exist_ok=True)
            command = [sys.executable, '-u', str(project / 'scripts/run_tisa_two_phase_annotation.py'),
                '--project-root', str(project), '--config', str(project / item['config']),
                '--env-file', str(project / '.env'), '--output-dir', str(output), '--allow-api']
            with (output / 'run.log').open('a', encoding='utf-8') as log:
                completed = subprocess.run(command, cwd=project, stdout=log, stderr=subprocess.STDOUT)
            return {'topic': item['topic'], 'returncode': completed.returncode, 'output_dir': str(output)}

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run, item) for item in suite['topics']]
            for future in as_completed(futures):
                row = future.result()
                state['completed'].append(row)
                atomic_write_json(root / 'suite_status.json', state)
                print(json.dumps(row), flush=True)
                if row['returncode'] == 3:
                    for item in suite['topics']:
                        (project / item['output_dir'] / 'STOP').touch()
        baseline_path = project / 'artifacts/phase1_span_audit_20260917/report.json'
        baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
        comparisons = []
        for item in suite['topics']:
            path = project / item['output_dir'] / 'gold_date_coverage.json'
            if not path.exists():
                comparisons.append({'topic': item['topic'], 'status': 'missing_evaluation'})
                continue
            new = json.loads(path.read_text(encoding='utf-8'))
            old = next(r for r in baseline['rows'] if r['dataset'] == item['dataset'] and r['topic'] == item['topic'])
            comparisons.append({'topic': item['topic'], 'baseline_hits': old['date_hits'],
                'baseline_total': old['gold_count'], 'baseline_recall': old['date_recall'],
                'new': new, 'change_percentage_points': 100 * (new['gold_date_recall'] - old['date_recall'])})
        report = {'finished_at_utc': datetime.now(timezone.utc).isoformat(), 'comparisons': comparisons,
                  'baseline_sha256': sha256(baseline_path), 'source_sha256': binding['source_sha256'],
                  'scope': 'Fresh phase one only; same article index/top-k, pre-extracted result representation.',
                  'limitations': 'Gold-selected diagnostics; historical versions and budgets differ, not a controlled ablation.'}
        atomic_write_json(root / 'comparison.json', report)
        state.update(stage='completed' if all(r['returncode'] == 0 for r in state['completed']) else 'completed_with_errors',
                     finished_at_utc=report['finished_at_utc'])
        atomic_write_json(root / 'suite_status.json', state)
    return 0 if state['stage'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
