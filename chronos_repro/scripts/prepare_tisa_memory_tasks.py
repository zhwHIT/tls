from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.data import iter_topics
from chronos_repro.gold_supervision import (
    GAP_TYPES,
    align_reference_events,
    build_masked_state,
    select_targets,
    validate_cross_dataset_topic_splits,
)
from chronos_repro.retrieval import read_index_metadata
from chronos_repro.snapshot import sha256
from chronos_repro.splitting import REQUIRED_SPLITS, validate_topic_splits
from chronos_repro.tisa_memory import REFINE, SKELETON, attach_phase_memory


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows),
        encoding='utf-8',
    )


def state_event(event: dict) -> dict:
    return {
        'event_id': event['event_id'],
        'time': {
            'value': event['canonical_date'],
            'granularity': 'day',
            'confidence': event['consensus'],
        },
        'summary': event['summary'],
        'actors': [],
        'location': None,
        'evidence_ids': [],
        'support': 0,
        'conflict': False,
    }


def sample_evenly(events: list[dict], maximum: int) -> list[dict]:
    if len(events) <= maximum:
        return list(events)
    if maximum <= 1:
        return [events[0]]
    indices = {
        round(position * (len(events) - 1) / (maximum - 1))
        for position in range(maximum)
    }
    return [events[index] for index in sorted(indices)]


def phase1_visible(events: list[dict], mode: str, maximum: int) -> list[dict]:
    if mode == 'empty':
        return []
    if mode == 'head':
        count = min(maximum, max(1, len(events) // 3))
        return events[:count]
    if mode == 'sparse':
        count = min(maximum, max(2, len(events) // 4))
        return sample_evenly(events, count)
    raise ValueError(f'Unsupported phase1 state mode: {mode}')


def compact_private_event(event: dict) -> dict:
    return {
        'event_id': event['event_id'],
        'canonical_date': event['canonical_date'],
        'accepted_dates': event['accepted_dates'],
        'summary': event['summary'],
        'reference_count': event['reference_count'],
        'reference_total': event['reference_total'],
        'consensus': event['consensus'],
    }


def phase1_rows(
    dataset: str,
    topic: str,
    split_name: str,
    events: list[dict],
    config: dict,
) -> list[tuple[dict, dict]]:
    rows = []
    maximum_visible = int(config.get('phase1_max_visible_events', 12))
    maximum_private = int(config.get('phase1_max_private_events', 16))
    for mode in config.get('phase1_state_modes', ['empty', 'head', 'sparse']):
        visible = phase1_visible(events, mode, maximum_visible)
        visible_ids = {event['event_id'] for event in visible}
        hidden = [event for event in events if event['event_id'] not in visible_ids]
        private_events = sample_evenly(hidden, maximum_private)
        if not private_events:
            continue
        base_state = {
            'topic': topic,
            'budget': {
                'queries_left': int(config.get('phase1_queries_left', 5)),
                'tokens_left': int(config.get('phase1_tokens_left', 6000)),
            },
            'events': [state_event(event) for event in visible],
            'gaps': [],
        }
        state = attach_phase_memory(base_state, SKELETON)
        task_id = f'{dataset}:{topic}:phase1:{mode}'
        policy = {
            'schema_version': 3,
            'task_id': task_id,
            'dataset': dataset,
            'topic': topic,
            'split': split_name,
            'phase': SKELETON,
            'state_mode': mode,
            'policy_state': state,
            'max_steps': int(config.get('phase1_max_steps', 8)),
            'allowed_actions': list(state['valid_actions']),
            'runtime_actions': ['SEARCH', 'VERIFY', 'MERGE', 'STOP'],
        }
        private = {
            'schema_version': 3,
            'task_id': task_id,
            'dataset': dataset,
            'topic': topic,
            'split': split_name,
            'private_target': {
                'task_kind': 'PHASE1_COVERAGE',
                'gold_events': [
                    compact_private_event(event) for event in private_events
                ],
                'gold_event_count_total': len(events),
                'gold_event_count_visible': len(visible),
            },
        }
        rows.append((policy, private))
    return rows


def phase2_rows(
    dataset: str,
    topic: str,
    split_name: str,
    events: list[dict],
    gap_counter: int,
    config: dict,
) -> tuple[list[tuple[dict, dict]], int]:
    rows = []
    targets = select_targets(events, int(config['max_targets_per_topic']))
    for target in targets:
        gap_type = GAP_TYPES[gap_counter % len(GAP_TYPES)]
        gap_counter += 1
        base_state = build_masked_state(
            topic,
            events,
            target,
            gap_type,
            int(config.get('phase2_queries_left', config['queries_left'])),
        )
        state = attach_phase_memory(base_state, REFINE)
        task_id = f'{dataset}:{topic}:{target["event_id"]}:{gap_type.lower()}:phase2'
        policy = {
            'schema_version': 3,
            'task_id': task_id,
            'dataset': dataset,
            'topic': topic,
            'split': split_name,
            'phase': REFINE,
            'policy_state': state,
            'max_steps': int(config.get('phase2_max_steps', 6)),
            'allowed_actions': list(state['valid_actions']),
            'runtime_actions': ['SEARCH', 'VERIFY', 'MERGE', 'STOP'],
        }
        private_target = dict(target)
        private_target['task_kind'] = 'PHASE2_GAP'
        private_target['gap_type'] = gap_type
        private = {
            'schema_version': 3,
            'task_id': task_id,
            'dataset': dataset,
            'topic': topic,
            'split': split_name,
            'private_target': private_target,
        }
        rows.append((policy, private))
    return rows, gap_counter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config_path = Path(args.config).resolve()
    output_dir = Path(args.output_dir).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    policy_rows = {name: [] for name in REQUIRED_SPLITS}
    private_rows = {name: [] for name in REQUIRED_SPLITS}
    phase_counts = {
        name: {SKELETON: 0, REFINE: 0} for name in REQUIRED_SPLITS
    }
    dataset_manifest = {}
    gap_counter = 0
    assignments = {
        dataset: validate_topic_splits(dataset_config['splits'])
        for dataset, dataset_config in config['datasets'].items()
    }
    checked_groups = validate_cross_dataset_topic_splits(
        assignments, config['split_policy']['cross_dataset_groups']
    )

    for dataset, dataset_config in config['datasets'].items():
        data_root = project_root / dataset_config['data']
        index = project_root / dataset_config['index']
        topics = list(iter_topics(data_root))
        actual = {topic.topic_id for topic in topics}
        expected = set(assignments[dataset])
        if actual != expected:
            raise ValueError(
                f'{dataset} split mismatch: missing={sorted(actual - expected)}, '
                f'unknown={sorted(expected - actual)}'
            )
        aligned_count = 0
        for topic in topics:
            events = align_reference_events(
                topic.topic_id,
                topic.timelines,
                int(config['date_alignment_window_days']),
                float(config['summary_similarity_threshold']),
            )
            aligned_count += len(events)
            split_name = assignments[dataset][topic.topic_id]
            pairs = phase1_rows(
                dataset, topic.topic_id, split_name, events, config
            )
            phase2, gap_counter = phase2_rows(
                dataset,
                topic.topic_id,
                split_name,
                events,
                gap_counter,
                config,
            )
            pairs.extend(phase2)
            for policy, private in pairs:
                policy_rows[split_name].append(policy)
                private_rows[split_name].append(private)
                phase_counts[split_name][policy['phase']] += 1
        metadata = read_index_metadata(index)
        dataset_manifest[dataset] = {
            'snapshot': metadata['snapshot'],
            'index_sha256': sha256(index),
            'topic_count': len(topics),
            'aligned_event_count': aligned_count,
        }

    outputs = {}
    for split_name in REQUIRED_SPLITS:
        policy_path = output_dir / split_name / 'policy_tasks.jsonl'
        private_path = output_dir / split_name / 'private_targets.jsonl'
        write_jsonl(policy_path, policy_rows[split_name])
        write_jsonl(private_path, private_rows[split_name])
        outputs[split_name] = {
            'policy': {
                'file': policy_path.relative_to(output_dir).as_posix(),
                'rows': len(policy_rows[split_name]),
                'sha256': sha256(policy_path),
            },
            'private': {
                'file': private_path.relative_to(output_dir).as_posix(),
                'rows': len(private_rows[split_name]),
                'sha256': sha256(private_path),
            },
            'phase_counts': phase_counts[split_name],
            'topics': sorted({row['topic'] for row in policy_rows[split_name]}),
        }
    all_ids = [
        {row['task_id'] for row in policy_rows[name]} for name in REQUIRED_SPLITS
    ]
    if all_ids[0] & all_ids[1] or all_ids[0] & all_ids[2] or all_ids[1] & all_ids[2]:
        raise AssertionError('Task leakage detected between splits')
    manifest = {
        'schema_version': 3,
        'config': config_path.name,
        'config_sha256': sha256(config_path),
        'datasets': dataset_manifest,
        'outputs': outputs,
        'checks': {
            'topic_config_complete': True,
            'task_overlap': False,
            'cross_dataset_groups_checked': len(checked_groups),
            'policy_private_row_counts_match': all(
                len(policy_rows[name]) == len(private_rows[name])
                for name in REQUIRED_SPLITS
            ),
            'test_private_targets_separate': True,
        },
        'teacher_boundary': {
            'allowed': 'train/policy_tasks.jsonl + train/private_targets.jsonl',
            'forbidden': ['dev/private_targets.jsonl', 'test/private_targets.jsonl'],
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / 'tisa_memory_tasks_v3_manifest.json'
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


if __name__ == '__main__':
    main()
