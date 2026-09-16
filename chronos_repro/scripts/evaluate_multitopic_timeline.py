"""Offline multi-topic strict-date evaluation. This script has no API client."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.full_timeline import evaluate_gold_coverage, prediction_from_timeline
from chronos_repro.multitopic_evaluation import summarize_topics
from chronos_repro.snapshot import MANIFEST, sha256


def verify_run_binding(project, item, output, config, trajectory):
    """Recheck the actual frozen topic, not just the run's descriptive labels."""
    if not config.get('coverage_pipeline', {}).get('enabled'):
        return 'not_checked_legacy_run'
    binding = json.loads((output / 'run_binding.json').read_text(encoding='utf-8'))
    config_path = (project / item['config']).resolve()
    if not config_path.is_relative_to(project) or sha256(config_path) != binding['config_sha256']:
        raise ValueError('Bound original configuration hash changed')
    if json.loads(config_path.read_text(encoding='utf-8')) != config:
        raise ValueError('Saved run configuration differs from bound original configuration')
    if trajectory.get('pipeline_revision') != config['pipeline_revision']:
        raise ValueError('Trajectory revision differs from run configuration')
    data = (project / config['data']).resolve()
    if not data.is_relative_to(project):
        raise ValueError('Bound dataset must remain inside project')
    manifest_path = data / MANIFEST
    if sha256(manifest_path) != binding['snapshot_manifest_sha256']:
        raise ValueError('Frozen snapshot manifest changed')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['snapshot_id'] != binding['snapshot_id']:
        raise ValueError('Snapshot identity changed')
    entries = [row for row in manifest['files'] if row['path'].startswith(item['topic'] + '/')]
    if not entries:
        raise ValueError('Bound topic is absent from snapshot')
    for row in entries:
        source = (data / row['path']).resolve()
        if not source.is_relative_to(data) or not source.is_file() or sha256(source) != row['sha256']:
            raise ValueError('Frozen topic file changed: ' + row['path'])
    return 'config_and_frozen_topic_hashes_verified'


def evaluate_suite(project, suite):
    rows = []
    expected = [(r['dataset'], r['topic']) for r in suite['topics']]
    for item in suite['topics']:
        output = (project / item['output_dir']).resolve()
        if not output.is_relative_to(project):
            raise ValueError('Run output must remain inside project')
        trajectory_path = output / 'trajectory.json'
        config_path = output / 'run_config.json'
        if not trajectory_path.is_file():
            continue
        if not config_path.is_file():
            raise ValueError(f'Missing bound run config: {output}')
        trajectory = json.loads(trajectory_path.read_text(encoding='utf-8'))
        config = json.loads(config_path.read_text(encoding='utf-8'))
        for value in (trajectory, config):
            if (value.get('dataset'), value.get('topic')) != (item['dataset'], item['topic']):
                raise ValueError('Run topic does not match planned evaluation topic')
        if config.get('phase2_teacher_guidance', True):
            raise ValueError('Gold-guided runs cannot enter autonomous evaluation')
        if config.get('pipeline_revision') != suite['pipeline_revision']:
            raise ValueError('Cannot pool runs of different framework revisions')
        integrity = verify_run_binding(project, item, output, config, trajectory)
        code_path = output / 'code_binding.json'
        source_version = 'unbound_legacy'
        if code_path.exists():
            code = json.loads(code_path.read_text(encoding='utf-8'))
            source_version = hashlib.sha256(json.dumps(code['files'], sort_keys=True).encode()).hexdigest()
            if code['source_sha256'] != source_version or trajectory.get('source_sha256') != source_version:
                raise ValueError('Trajectory source binding differs; replay may still be in progress')
        data_root = (project / config['data']).resolve()
        if not data_root.is_relative_to(project):
            raise ValueError('Dataset must remain inside project')
        topic = next(t for t in iter_topics(data_root) if t.topic_id == item['topic'])
        events = trajectory['final_events']
        coverage = evaluate_gold_coverage(item['topic'], prediction_from_timeline(events),
                                          topic.timelines, config.get('semantic_coverage_threshold', .2))
        rows.append({'dataset': item['dataset'], 'topic': item['topic'],
                     'status': trajectory['status'], 'gold_date_hits': coverage['covered_gold_dates'],
                     'gold_date_count': coverage['gold_date_count'],
                     'predicted_date_count': len({str(e['time']) for e in events}),
                     'output_dir': item['output_dir'], 'input_integrity': integrity,
                     'continuation': trajectory.get('continuation'),
                     'source_sha256': source_version})
    result = summarize_topics(rows, expected, suite.get('target_gold_date_recall', .7))
    versions = {row['source_sha256'] for row in rows}
    result['comparable_source_versions'] = len(versions) == 1 and 'unbound_legacy' not in versions
    if not result['comparable_source_versions']:
        result['aggregate'] = None
    result.update(api_calls=0, pipeline_revision=suite['pipeline_revision'],
                  evaluation_mode='state_continuation' if any(r.get('continuation') for r in rows) else 'fresh_rollout',
                  split=suite.get('split', 'unassigned'),
                  limitations=['Strict date coverage does not establish event correctness',
                               'Previously inspected dev topics are not an untouched test set'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--suite', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    project = Path(args.project_root).resolve()
    suite = json.loads(Path(args.suite).read_text(encoding='utf-8'))
    report = evaluate_suite(project, suite)
    destination = Path(args.output).resolve()
    if not destination.is_relative_to(project):
        raise ValueError('Evaluation report must remain inside project')
    if destination.exists():
        raise FileExistsError('Use a new report path; do not overwrite an existing evaluation')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['all_complete'] and report['comparable_source_versions'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
