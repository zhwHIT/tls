import json
from pathlib import Path

import pytest

from chronos_repro.snapshot import MANIFEST, sha256


@pytest.mark.parametrize('tamper', [None, 'source_config', 'saved_config', 'gold', 'manifest', 'revision'])
def test_evaluation_binding_detects_changed_inputs(tmp_path, monkeypatch, tamper):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from evaluate_multitopic_timeline import verify_run_binding
    topic = tmp_path / 'data/Aurora'
    topic.mkdir(parents=True)
    gold = topic / 'timelines.jsonl'
    gold.write_text('{}', encoding='utf-8')
    manifest_path = tmp_path / 'data' / MANIFEST
    manifest = {'snapshot_id': 'fixture', 'files': [
        {'path': 'Aurora/timelines.jsonl', 'sha256': sha256(gold)}]}
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    config = {'coverage_pipeline': {'enabled': True}, 'data': 'data', 'pipeline_revision': 'v7'}
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    binding = {'config_sha256': sha256(config_path), 'snapshot_manifest_sha256': sha256(manifest_path),
               'snapshot_id': 'fixture'}
    (tmp_path / 'run_binding.json').write_text(json.dumps(binding), encoding='utf-8')
    trajectory = {'pipeline_revision': 'v7'}
    if tamper == 'source_config':
        config_path.write_text('{}', encoding='utf-8')
    elif tamper == 'saved_config':
        config['extra'] = True
    elif tamper == 'gold':
        gold.write_text('{"changed":true}', encoding='utf-8')
    elif tamper == 'manifest':
        manifest_path.write_text('{}', encoding='utf-8')
    elif tamper == 'revision':
        trajectory['pipeline_revision'] = 'v8'
    item = {'config': 'config.json', 'topic': 'Aurora'}
    if tamper:
        with pytest.raises(ValueError):
            verify_run_binding(tmp_path, item, tmp_path, config, trajectory)
    else:
        assert verify_run_binding(tmp_path, item, tmp_path, config, trajectory) == 'config_and_frozen_topic_hashes_verified'
