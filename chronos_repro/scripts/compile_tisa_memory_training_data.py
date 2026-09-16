from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.annotation_boundary import (
    load_train_annotation_pairs,
    read_jsonl,
)
from chronos_repro.snapshot import sha256
from chronos_repro.tisa_data import TOOLS, tool_call, tool_observation
from chronos_repro.tisa_memory import (
    REFINE,
    SKELETON,
    rank_query_candidates,
    validate_controller_review,
    validate_policy_state,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows),
        encoding='utf-8',
    )


def controller_content(thought: dict, memory_update: dict) -> str:
    return json.dumps(
        {'thought': thought, 'memory_update': memory_update},
        ensure_ascii=False,
        sort_keys=True,
    )


def action_arguments(
    action: str,
    policy: dict,
    query: str,
    memory_update: dict,
) -> dict:
    if action == 'SEARCH':
        return {
            'query': query,
            'dataset': policy['dataset'],
            'topic': policy['topic'],
            'top_k': 12,
        }
    if action == 'SWITCH_PHASE':
        return {
            'reason': 'skeleton is sufficiently covered',
            'memory': memory_update,
        }
    if action == 'STOP':
        return {
            'reason': 'timeline is sufficiently covered',
            'confidence': 0.8,
        }
    raise ValueError(f'Unsupported controller action: {action}')


def assistant_action(
    call_id: str,
    action: str,
    policy: dict,
    query: str,
    thought: dict,
    memory_update: dict,
) -> dict:
    message = tool_call(
        call_id,
        action,
        action_arguments(action, policy, query, memory_update),
    )
    message['content'] = controller_content(thought, memory_update)
    return message


def preference_row(
    trajectory_id: str,
    decision: str,
    prompt: list[dict],
    chosen: dict,
    rejected: dict,
    scores: dict,
) -> dict:
    score = scores[decision.casefold()]
    return {
        'schema_version': 3,
        'trajectory_id': trajectory_id,
        'decision': decision,
        'prompt': prompt,
        'chosen': [chosen],
        'rejected': [rejected],
        'metadata': {
            'label_source': 'llm_teacher_frozen_replay_gold_validated',
            'chosen_score': float(score['chosen']),
            'rejected_score': float(score['rejected']),
            'margin': float(score['chosen']) - float(score['rejected']),
        },
    }


def history_from_state(state: dict) -> tuple[list[str], list[str]]:
    memory = state['memory']
    if state['phase'] == SKELETON:
        covered = list(memory.get('covered_dates', []))
        rows = memory.get('query_history', [])
    else:
        covered = [
            str(event.get('time', {}).get('value'))[:10]
            for event in state.get('events', [])
            if event.get('time', {}).get('value')
        ]
        rows = memory.get('attempted_queries', [])
    queries = [
        str(row.get('query', '')) if isinstance(row, dict) else str(row)
        for row in rows
    ]
    return covered, [query for query in queries if query]


def compile_one(
    policy: dict,
    private: dict,
    label: dict,
    margin: float,
) -> tuple[dict, list[dict], dict]:
    task_id = policy['task_id']
    if label.get('task_id') != task_id or label.get('split') != 'train':
        raise ValueError(f'Controller label identity/split mismatch: {task_id}')
    state = policy['policy_state']
    validate_policy_state(state)
    proposal = label['proposal']
    review = label['controller_review']
    candidates = [str(query) for query in proposal['candidate_queries']]
    results_by_query = {
        query: label['retrieval'][query] for query in candidates
    }
    covered, history = history_from_state(state)
    ranking = rank_query_candidates(
        candidates,
        results_by_query,
        private['private_target'],
        covered,
        history,
    )
    validate_controller_review(
        review,
        state['phase'],
        candidates,
        ranking,
        margin,
    )
    if review['chosen_action'] != 'SEARCH':
        raise ValueError('Current v3 compiler expects an unresolved SEARCH state')

    system = {
        'role': 'system',
        'content': (
            'Use phase-specific memory to choose SEARCH, SWITCH_PHASE, or STOP. '
            'VERIFY and MERGE are frozen executor modules.'
        ),
    }
    user = {
        'role': 'user',
        'content': json.dumps(state, ensure_ascii=False, sort_keys=True),
    }
    prompt = [system, user]
    thought = proposal['thought']
    chosen_query = str(review['chosen_query'])
    rejected_query = str(review['rejected_query'])
    chosen_memory = review['memory_update_chosen']
    rejected_memory = review['memory_update_rejected']
    chosen_action = str(review['chosen_action'])
    rejected_action = str(review['rejected_action'])
    chosen = assistant_action(
        'controller-search',
        chosen_action,
        policy,
        chosen_query,
        thought,
        chosen_memory,
    )
    rejected_query_message = assistant_action(
        'rejected-query',
        'SEARCH',
        policy,
        rejected_query,
        thought,
        chosen_memory,
    )
    rejected_action_message = assistant_action(
        'rejected-action',
        rejected_action,
        policy,
        rejected_query,
        thought,
        rejected_memory,
    )
    rejected_memory_message = assistant_action(
        'rejected-memory',
        chosen_action,
        policy,
        chosen_query,
        thought,
        rejected_memory,
    )
    selected_documents = label['retrieval'][chosen_query]
    messages = list(prompt)
    messages.append(chosen)
    messages.append(
        tool_observation(
            'controller-search',
            {
                'documents': selected_documents,
                'next_executor': 'VERIFY',
            },
        )
    )
    trajectory_id = f'{task_id}:memory-controller-v3'
    sft = {
        'schema_version': 3,
        'trajectory_id': trajectory_id,
        'tools': TOOLS,
        'messages': messages,
        'metadata': {
            'dataset': policy['dataset'],
            'topic': policy['topic'],
            'split': 'train',
            'phase': state['phase'],
            'label_source': 'llm_teacher_frozen_replay_gold_validated',
            'trainable_module': 'controller',
            'frozen_modules': ['retriever', 'verify', 'merge', 'writer'],
        },
    }
    scores = review['preference_scores']
    dpo = [
        preference_row(
            trajectory_id,
            'QUERY',
            prompt,
            chosen,
            rejected_query_message,
            scores,
        ),
        preference_row(
            trajectory_id,
            'ACTION',
            prompt,
            chosen,
            rejected_action_message,
            scores,
        ),
        preference_row(
            trajectory_id,
            'MEMORY',
            prompt,
            chosen,
            rejected_memory_message,
            scores,
        ),
    ]
    episode = {
        'schema_version': 3,
        'trajectory_id': trajectory_id,
        'initial_state': state,
        'steps': [
            {
                'valid_actions': policy['allowed_actions'],
                'action': {
                    'type': chosen_action,
                    'arguments': action_arguments(
                        chosen_action,
                        policy,
                        chosen_query,
                        chosen_memory,
                    ),
                },
                'memory_update': chosen_memory,
                'observation': {'documents': selected_documents},
                'reward': float(scores['action']['chosen']),
                'terminal': False,
            }
        ],
        'terminal': False,
    }
    return sft, dpo, episode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks-root', required=True)
    parser.add_argument('--controller-labels', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--preference-margin', type=float, default=0.2)
    args = parser.parse_args()
    root = Path(args.tasks_root).resolve()
    pairs = load_train_annotation_pairs(
        root / 'train' / 'policy_tasks.jsonl',
        root / 'train' / 'private_targets.jsonl',
    )
    pair_by_id = {
        policy['task_id']: (policy, private) for policy, private in pairs
    }
    labels = read_jsonl(Path(args.controller_labels).resolve())
    label_by_id = {row['task_id']: row for row in labels}
    if len(label_by_id) != len(labels):
        raise ValueError('Duplicate controller task_id')
    unknown = sorted(set(label_by_id) - set(pair_by_id))
    if unknown:
        raise ValueError(f'Controller labels not present in train tasks: {unknown}')

    sft_rows, dpo_rows, episode_rows = [], [], []
    for task_id in sorted(label_by_id):
        policy, private = pair_by_id[task_id]
        sft, dpo, episode = compile_one(
            policy,
            private,
            label_by_id[task_id],
            args.preference_margin,
        )
        sft_rows.append(sft)
        dpo_rows.extend(dpo)
        episode_rows.append(episode)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        'controller_sft': output / 'tisa_memory_controller_sft_v3.jsonl',
        'controller_dpo': output / 'tisa_memory_controller_dpo_v3.jsonl',
        'controller_episodes': output / 'tisa_memory_controller_episodes_v3.jsonl',
    }
    rows = {
        'controller_sft': sft_rows,
        'controller_dpo': dpo_rows,
        'controller_episodes': episode_rows,
    }
    for name, path in paths.items():
        write_jsonl(path, rows[name])
    manifest = {
        'schema_version': 3,
        'scope': 'train_only',
        'counts': {name: len(value) for name, value in rows.items()},
        'decisions': ['QUERY', 'ACTION', 'MEMORY'],
        'outputs': {
            name: {'file': path.name, 'sha256': sha256(path)}
            for name, path in paths.items()
        },
    }
    (output / 'tisa_memory_training_v3_manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


if __name__ == '__main__':
    main()
