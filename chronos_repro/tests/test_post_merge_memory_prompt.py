import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from exploration_phase import memory_instruction, post_merge_memory_instruction, policy_instruction


def test_post_merge_schema_does_not_request_reextraction():
    prompt = post_merge_memory_instruction({'events': []})
    assert prompt['required_json']['observations'] == []
    assert prompt['required_json']['discovered_keywords'] == []
    assert prompt['output_limits']['maximum_observations'] == 0
    assert prompt['output_limits']['maximum_keywords'] == 0
    assert 'verified event ID' in prompt['required_json']['stage_outline'][0]['evidence_ids'][0]


def test_legacy_pre_merge_prompt_keeps_its_extraction_contract():
    post_merge_memory_instruction({})
    assert memory_instruction({})['required_json']['observations']


def test_policy_and_memory_both_prioritize_coarse_stages():
    for prompt in (post_merge_memory_instruction({}), policy_instruction({})):
        assert 'Pending leads are reminders for phase two' in prompt['objective']
        assert 'publication-year counts only' in prompt['objective']
