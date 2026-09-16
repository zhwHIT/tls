from chronos_repro.tisa_memory import REFINE, attach_phase_memory
from scripts.compile_tisa_memory_training_data import compile_one


def test_compiles_controller_sft_and_three_local_preferences():
    state = attach_phase_memory(
        {
            'topic': 'egypt',
            'budget': {'queries_left': 3, 'tokens_left': 4000},
            'events': [
                {
                    'event_id': 'e1',
                    'time': {'value': '2020-01-01'},
                    'summary': 'earlier event',
                    'support': 1,
                    'conflict': False,
                }
            ],
            'gaps': [
                {
                    'gap_id': 'g1',
                    'type': 'MISSING_DATE',
                    'priority': 1.0,
                    'window_start': '2020-01-01',
                    'window_end': '2020-03-01',
                }
            ],
        },
        REFINE,
    )
    policy = {
        'task_id': 'crisis:egypt:test:phase2',
        'dataset': 'crisis',
        'topic': 'egypt',
        'split': 'train',
        'phase': REFINE,
        'policy_state': state,
        'allowed_actions': ['SEARCH', 'STOP'],
    }
    private = {
        'task_id': policy['task_id'],
        'dataset': 'crisis',
        'topic': 'egypt',
        'split': 'train',
        'private_target': {
            'accepted_dates': ['2020-02-01'],
            'summary': 'target event',
        },
    }
    candidates = [
        'egypt target event date',
        'egypt broad political timeline',
        'egypt unrelated economy story',
    ]
    label = {
        'task_id': policy['task_id'],
        'split': 'train',
        'proposal': {
            'thought': {
                'search_need': 'HIGH',
                'target': 'missing date',
                'reason_code': 'MISSING_DATE',
            },
            'candidate_queries': candidates,
        },
        'controller_review': {
            'chosen_action': 'SEARCH',
            'rejected_action': 'STOP',
            'chosen_query': candidates[0],
            'rejected_query': candidates[1],
            'memory_update_chosen': {
                'active_target': 'missing date',
                'last_query': candidates[0],
                'gap_status': 'OPEN',
            },
            'memory_update_rejected': {
                'active_target': 'none',
                'gap_status': 'CLOSED',
            },
            'preference_scores': {
                name: {'chosen': 0.9, 'rejected': 0.5}
                for name in ('query', 'action', 'memory')
            },
        },
        'retrieval': {
            candidates[0]: [
                {'id': 'd1', 'date': '2020-02-01', 'title': 'target'}
            ],
            candidates[1]: [
                {'id': 'd2', 'date': '2020-01-01', 'title': 'known'}
            ],
            candidates[2]: [],
        },
    }
    sft, dpo, episode = compile_one(policy, private, label, 0.2)
    assert sft['metadata']['trainable_module'] == 'controller'
    assert [row['decision'] for row in dpo] == ['QUERY', 'ACTION', 'MEMORY']
    assert episode['steps'][0]['action']['type'] == 'SEARCH'
