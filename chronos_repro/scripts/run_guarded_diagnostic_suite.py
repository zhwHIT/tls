"""Serial authorized diagnostic execution; never reset a topic's request ledger."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from chronos_repro.run_guard import exclusive_run, source_binding


def plan(project, suite, *, allow_exhausted=False):
    rows = []
    for item in suite['topics']:
        output = (project / item['output_dir']).resolve()
        config_path = (project / item['config']).resolve()
        if not output.is_relative_to(project) or not config_path.is_relative_to(project):
            raise ValueError('Suite paths must remain inside project')
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if (config['dataset'], config['topic']) != (item['dataset'], item['topic']):
            raise ValueError('Suite and configuration topic disagree')
        if config.get('phase2_teacher_guidance', True):
            raise ValueError('Autonomous diagnostics must not send private references')
        ledger_path = output / 'request_ledger.json'
        ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else None
        if ledger and (ledger['request_limit'] != config['max_api_requests'] or ledger.get('balance_stop')):
            raise ValueError('Changed authorization ceiling or balance stop: halt entire suite')
        used = ledger['requests_started'] if ledger else 0
        if ledger is None and config.get('continuation'):
            parent_path = (project / config['continuation']['snapshot']).resolve().parent
            if not parent_path.is_relative_to(project):
                raise ValueError('Continuation source must remain inside project')
            parent_ledger = json.loads((parent_path / 'request_ledger.json').read_text(encoding='utf-8'))
            if parent_ledger.get('balance_stop'):
                raise ValueError('Parent balance stop: halt entire suite')
            used = parent_ledger['requests_started']
        if type(used) is not int or not 0 <= used <= config['max_api_requests'] or (
                used == config['max_api_requests'] and not allow_exhausted):
            raise ValueError('Invalid or exhausted cumulative request ledger')
        rows.append({**item, 'requests_already_started': used,
                     'remaining_http_requests': config['max_api_requests'] - used})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--suite', required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--record-dir', required=True)
    parser.add_argument('--allow-api', action='store_true')
    parser.add_argument('--partitions', type=int, default=1)
    parser.add_argument('--partition', type=int, default=0)
    parser.add_argument('--evaluate-after-each', action='store_true',
                        help='Write an immutable offline evaluation after each attempted topic')
    args = parser.parse_args()
    project = Path(args.project_root).resolve()
    suite = json.loads(Path(args.suite).read_text(encoding='utf-8'))
    if not 0 <= args.partition < args.partitions:
        parser.error('partition must be in [0, partitions)')
    rows = plan(project, suite, allow_exhausted=True)[args.partition::args.partitions]
    if not args.allow_api:
        print(json.dumps({'api_calls': 0, 'plan': rows}, indent=2))
        return 0
    record_dir = Path(args.record_dir).resolve()
    if not record_dir.is_relative_to(project):
        raise ValueError('Record directory must remain inside project')
    with exclusive_run(record_dir):
        status_path = record_dir / 'batch_status.json'
        if status_path.exists():
            raise ValueError('Use a new batch record directory; preserve previous batch records')
        fingerprint = source_binding(project)['source_sha256']
        record = {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'running',
                  'source_sha256': fingerprint, 'plan': rows, 'completed_processes': []}
        def save():
            temporary = status_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
            from chronos_repro.atomic_io import replace_with_retry
            replace_with_retry(temporary, status_path)
        save()
        for row in rows:
            if source_binding(project)['source_sha256'] != fingerprint:
                record['status'] = 'stopped_source_changed'
                save()
                return 2
            # Recheck every ledger before starting another topic, including global balance stops.
            try:
                plan(project, suite, allow_exhausted=True)
            except ValueError as error:
                record.update(status='stopped_guard', error=str(error))
                save()
                return 2
            record['active_topic'] = row['dataset'] + ':' + row['topic']
            save()
            output = project / row['output_dir']
            current = next(r for r in plan(project, suite, allow_exhausted=True)
                           if (r['dataset'], r['topic']) == (row['dataset'], row['topic']))
            if current['remaining_http_requests'] == 0:
                record['completed_processes'].append({'topic': record['active_topic'],
                    'returncode': 2, 'status': 'skipped_exhausted_ledger'})
                save()
                continue
            command = [sys.executable, '-B', str(project / 'scripts/run_tisa_two_phase_annotation.py'),
                       '--project-root', str(project), '--config', str(project / row['config']),
                       '--env-file', str(Path(args.env_file).resolve()), '--output-dir', str(output), '--allow-api']
            if any((output / name).exists() for name in ('trajectory.json', 'checkpoint.json', 'request_ledger.json')):
                command.append('--replay-from-start')
            print('Starting ' + record['active_topic'] + '; cumulative ledger retained', flush=True)
            log_path = record_dir / (row['dataset'] + '_' + row['topic'] + '.log')
            with log_path.open('x', encoding='utf-8') as log:
                result = subprocess.run(command, cwd=project, stdout=log, stderr=subprocess.STDOUT)
            record['completed_processes'].append({'topic': record['active_topic'], 'returncode': result.returncode,
                                                   'log': str(log_path)})
            if args.evaluate_after_each:
                from evaluate_multitopic_timeline import evaluate_suite
                report = evaluate_suite(project, suite)
                report_path = record_dir / f"evaluation_after_{len(record['completed_processes']):02d}.json"
                with report_path.open('x', encoding='utf-8') as handle:
                    json.dump(report, handle, ensure_ascii=False, indent=2)
                    handle.write('\n')
                record['latest_evaluation'] = str(report_path)
                record['all_topics_complete'] = report['all_complete']
                record['aggregate'] = report['aggregate']
            if result.returncode == 3:
                record['status'] = 'stopped_insufficient_balance'
                save()
                return result.returncode
            save()
        errors = any(r['returncode'] for r in record['completed_processes'])
        record.update(status='completed_with_errors' if errors else 'completed', active_topic=None)
        save()
    return 2 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
