import json
from pathlib import Path

from chronos_repro.llm import ChatResult
from chronos_repro.tisa_memory import REFINE, attach_phase_memory
from scripts import annotate_tisa_memory_train as annotation


class FakeClient:
    model = 'fake-teacher'

    def __init__(self, payloads):
        self.payloads = list(payloads)

    def chat(self, messages, temperature=0.0):
        payload = self.payloads.pop(0)
        return ChatResult(
            text=json.dumps(payload),
            model=self.model,
            usage={'total_tokens': 10},
            request_id='fake',
        )


def test_repairable_call_keeps_usage_for_validation_failure():
    client = FakeClient([{'ok': False}, {'ok': True}])
    config = {'label_repair_attempts': 1, 'temperature': 0.0}

    def validator(payload):
        if not payload['ok']:
            raise ValueError('not ok')

    label, audits = annotation.repairable_call(
        client,
        {'stage': 'test'},
        config,
        validator,
    )
    assert label == {'ok': True}
    assert [row.get('usage', {}).get('total_tokens') for row in audits] == [
        10,
        None,
        10,
    ]
    assert audits[1]['validation_error'] == 'not ok'


def test_two_call_annotation_uses_frozen_replay_and_gold_validation(monkeypatch):
    candidates = [
        'egypt parliament opening date',
        'egypt broad political timeline',
        'egypt unrelated economy report',
    ]
    proposal = {
        'thought': {
            'search_need': 'HIGH',
            'target': 'missing parliament opening date',
            'reason_code': 'MISSING_DATE',
        },
        'memory_update_draft': {
            'active_target': 'missing date',
            'reason_code': 'MISSING_DATE',
        },
        'candidate_queries': candidates,
    }
    review = {
        'chosen_action': 'SEARCH',
        'rejected_action': 'STOP',
        'chosen_query': candidates[0],
        'rejected_query': candidates[1],
        'action_reason': 'Gold-window evidence is still reachable',
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
    }

    def fake_search(index, queries, top_k, engine):
        query = queries[0]
        if query == candidates[0]:
            return [
                {
                    'id': 'd1',
                    'timestamp': '2020-02-01',
                    'title': 'Parliament opens',
                    'snippet': 'supported evidence',
                }
            ]
        if query == candidates[1]:
            return [
                {
                    'id': 'd2',
                    'timestamp': '2020-01-01',
                    'title': 'Known event',
                    'snippet': 'already covered',
                }
            ]
        return []

    monkeypatch.setattr(annotation, 'search', fake_search)
    state = attach_phase_memory(
        {
            'topic': 'egypt',
            'budget': {'queries_left': 3, 'tokens_left': 4000},
            'events': [
                {
                    'event_id': 'e1',
                    'time': {'value': '2020-01-01'},
                    'summary': 'known event',
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
        'policy_state': state,
    }
    private = {
        'task_id': policy['task_id'],
        'dataset': 'crisis',
        'topic': 'egypt',
        'split': 'train',
        'private_target': {
            'accepted_dates': ['2020-02-01'],
            'summary': 'Egypt new parliament opening inaugural session',
        },
    }
    config = {
        'label_repair_attempts': 0,
        'temperature': 0.0,
        'minimum_query_candidates': 3,
        'maximum_query_candidates': 4,
        'maximum_query_gold_copy_ratio': 0.8,
        'top_k': 12,
        'review_top_docs_per_query': 4,
        'review_snippet_chars': 300,
        'preference_margin': 0.2,
    }
    label = annotation.annotate_one(
        FakeClient([proposal, review]),
        policy,
        private,
        Path('unused.sqlite3'),
        config,
    )
    assert label['controller_review']['chosen_action'] == 'SEARCH'
    assert label['query_ranking'][0]['query'] == candidates[0]
    assert label['query_ranking'][0]['metrics']['exact_gold_gain'] == 1
