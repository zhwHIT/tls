import json
import sqlite3
import sys
from pathlib import Path

import pytest

from chronos_repro.sentence_reader import SentenceUnionReader, contains, sentence_candidates
from chronos_repro.evidence_access import _passage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def make_reader(tmp_path, second='David Beckham left yesterday.'):
    db = tmp_path / 'docs.sqlite3'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE documents (doc_id TEXT,title TEXT,text TEXT,timestamp TEXT,topic TEXT)')
        con.executemany('INSERT INTO documents VALUES (?,?,?,?,?)', [
            ('a', 'David Beckham', 'David Beckham appeared yesterday in London.', '2008-03-27', 'David_Beckham'),
            ('b', 'David Beckham', second, '2008-03-27', 'David_Beckham')])
    reader = SentenceUnionReader(db, 'David_Beckham', {'passage_words': 80, 'passage_chars': 700,
        'overlap_words': 10, 'passages_per_search': 1,
        'sentence_tokenizer_path': str(ROOT / 'artifacts/deepseek_v4_tokenizer/tokenizer.json')})
    reader.add_results([{'id': 'a'}, {'id': 'b'}])
    baseline = _passage(reader.documents['a'], reader.documents['a']['text'], 0, len(reader.documents['a']['text']))
    baseline['context_before'] = ''
    reader.baseline.select = lambda *a, **k: [baseline]
    return reader


def test_queue_persists_and_selection_is_not_successful_extraction(tmp_path):
    reader = make_reader(tmp_path)
    first = reader.select('David Beckham appeared')
    assert [p['document_id'] for p in first] == ['a']
    assert reader.processed == set()
    reader.mark_processed(first)
    reader.add_results([{'id': 'a'}])
    second = reader.select('David Beckham appeared')
    assert [p['document_id'] for p in second] == ['b']
    assert reader.selection_history[-1]['choices'][0]['outside_current_top12']
    assert second[0]['id'] not in reader.processed
    assert reader.manifest()['unread_candidates'] == []
    for row in reader.selection_history:
        assert row['tokens'] <= row['token_budget']
        assert row['visible_chars'] <= row['visible_char_budget']


def test_oversized_candidate_remains_unread_without_clipping(tmp_path):
    reader = make_reader(tmp_path, 'David Beckham yesterday ' + 'evidence ' * 300 + '.')
    docs = reader.select('David Beckham appeared')
    assert all(p['document_id'] != 'b' for p in docs)
    assert any(c['document_id'] == 'b' and reader.budget_skips[c['id']] > 0 for c in reader.pending.values())
    for p in docs:
        assert p['text'] == reader.documents[p['document_id']]['text'][p['source_start']:p['source_end']]
        assert p['context_before'] == ''
    assert not contains([[0, 4], [5, 9]], 0, 9)


def test_online_sentence_units_match_offline_policy():
    sys.path.insert(0, str(ROOT / 'analysis_tools'))
    from experiment_beckham_sentence_queue import sentence_candidates as offline_candidates
    doc = {'id': 'd', 'title': 'David Beckham', 'text': 'David Beckham appeared last night.\nHe played his final match.\nTiny\nA longer unit ends here.'}
    assert sentence_candidates(doc) == offline_candidates(doc)


def test_first_phase_boundary_never_calls_second_phase_or_final_select(monkeypatch):
    import run_tisa_two_phase_annotation as runner
    def prohibited(*a, **k):
        raise AssertionError('API crossed the first-phase boundary')
    monkeypatch.setattr(runner, 'run_phase2', prohibited)
    monkeypatch.setattr(runner.coverage_pipeline, 'finalize', prohibited)
    state = {'timeline_events': [{'time': '2008-03-26'}]}
    assert runner.run_requested_later_phases(None, state, [], {'first_phase_only': True}, None, {}, {}) == {}
    assert state['event_pool'] == state['timeline_events']
    assert state['event_pool'] is not state['timeline_events']
    assert state['phase2_termination'] == 'not_requested_first_phase_only'


def test_first_phase_envelope_and_supplement_exclusion():
    import coverage_preflight
    config = json.loads((ROOT / 'configs/beckham_sentence_queue_phase1_api.json').read_text(encoding='utf-8'))
    scope = coverage_preflight.coverage_envelope(config)
    assert scope['max_search_rounds'] == 24 and scope['supplement_search_rounds'] == 0
    assert scope['gold_sent_to_model'] is False and scope['first_phase_only']
