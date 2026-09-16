"""Replay the deployed v8.4 packet transform on recorded v8.3 requests, without API."""
import json
from pathlib import Path

from chronos_repro.compact_context import compact_gap_update, serialize


def main():
    reports = []
    for name in ('crisis_yemen', 't17_mj', 'entities_David_Bowie'):
        source = Path('artifacts/tisa_v7_diagnostic') / name / 'controller_exact.review_required.jsonl'
        rows = []
        for line in source.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            original = json.loads(row['messages'][1]['content'])
            if 'update GAP_MEMORY' not in original.get('stage', ''):
                continue
            result = compact_gap_update(original)
            assert result['student_visible_state'] == original['student_visible_state']
            old_obs, new_obs = original['cycle_observation'], result['cycle_observation']
            assert old_obs['all_extracted_candidates'] == new_obs['all_extracted_candidates']
            if 'verified_candidate_ids' in new_obs:
                lookup = {r['candidate_id']: r for r in new_obs['all_extracted_candidates']}
                assert [lookup[i] for i in new_obs['verified_candidate_ids']] == old_obs['verified_candidates']
            else:
                assert new_obs.get('verified_candidates') == old_obs.get('verified_candidates')
            assert compact_gap_update(result) == result
            system_chars = len(row['messages'][0]['content'])
            rows.append({'before_chars': system_chars + len(serialize(original)),
                         'after_chars': system_chars + len(serialize(result))})
        before, after = sum(r['before_chars'] for r in rows), sum(r['after_chars'] for r in rows)
        reports.append({'topic': name, 'checked_requests': len(rows), 'before_chars_total': before,
                        'after_chars_total': after, 'reduction': 1-after/before if before else 0,
                        'before_chars_max': max(r['before_chars'] for r in rows),
                        'after_chars_max': max(r['after_chars'] for r in rows)})
    report = {'runtime_revision': 'two-phase-v8.4-lossless-gap-context-dedup', 'api_calls': 0,
              'source_responses': 'v8.3 accepted gap update requests', 'topics': reports,
              'facts_and_state_preserved': True,
              'limitations': ['Character reduction is not token/VRAM measurement',
                              'No API behavior or coverage improvement has been established for v8.4']}
    target = Path('artifacts/tisa_v84_context_replay.json')
    with target.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
