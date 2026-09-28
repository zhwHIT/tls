"""Offline frozen-query retrieval diagnosis. No model API or runtime edits."""
import copy
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

from chronos_repro.retrieval import search_single, reciprocal_rank_fusion, _deduplicate, temporal_diversify
from chronos_repro.dense_retrieval import search_dense

PROJECT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / 'retrieval_diagnosis'
OUT.mkdir(exist_ok=True)
MANIFEST = json.loads((PROJECT / 'artifacts/crisis_collection_v3_hybrid_index.json').read_text(encoding='utf-8'))
BM25 = PROJECT / 'artifacts' / MANIFEST['bm25_index']
DENSE = PROJECT / 'artifacts' / MANIFEST['dense_index']
FUSION = MANIFEST['fusion']

PROBES = {
    'egypt': [('2011-02-02', 'Egypt Mubarak supporters Tahrir Square clashes February 2011'),
              ('2011-03-03', 'Egypt Prime Minister Ahmed Shafik resigns March 2011')],
    'libya': [('2011-04-30', 'Libya NATO airstrike Gaddafi son grandchildren killed April 2011'),
              ('2011-08-23', 'Libya rebels storm Bab al Aziziya compound Tripoli August 2011')],
    'syria': [('2012-05-25', 'Syria Houla massacre May 2012'),
              ('2012-08-06', 'Syria Prime Minister Riad Hijab defects August 2012')],
    'yemen': [('2011-09-18', 'Yemen Sanaa protesters government forces open fire September 2011'),
              ('2011-09-23', 'Yemen Saleh returns from Saudi Arabia September 2011')],
}


def fused_order(lexical, semantic, pool):
    fused = reciprocal_rank_fusion([
        ('bm25:0', float(FUSION['bm25_weight']), lexical[:pool]),
        ('dense:0', float(FUSION['dense_weight']), semantic[:pool])], rrf_k=int(FUSION['rrf_k']))
    deduped = _deduplicate(fused)
    return temporal_diversify(deduped, len(deduped), float(FUSION['temporal_diversity']))


def main():
    reports = []
    for topic in PROBES:
        state = json.loads((RUN / f'crisis_{topic}/checkpoint.json').read_text(encoding='utf-8'))['state']
        score = json.loads((RUN / f'crisis_{topic}/gold_date_coverage.json').read_text(encoding='utf-8'))
        missing = set(score['available_but_not_retrieved'])
        gold = set(score['matched_gold_dates']) | missing | set(score['retrieved_but_not_selected'])
        dates_by_doc = defaultdict(set)
        for line in (PROJECT / f'artifacts/llm_tls_extraction_v1/events/crisis/{topic}_events.jsonl').open(encoding='utf-8'):
            row = json.loads(line)
            dates_by_doc[str(row['source_id'])].update(e['event_date'] for e in row.get('events', [])
                if isinstance(e.get('event_date'), str) and len(e['event_date']) == 10)

        def dates_for(ids):
            return set().union(*(dates_by_doc[i] for i in ids)) if ids else set()

        aggregate = {k: set() for k in ['fixed12', 'fixed24', 'fixed50', 'fixed_all', 'native24', 'native50', 'candidate_union']}
        best_rank = {}
        replay = []
        mismatches = []
        for number, query in enumerate(state['search_history'], 1):
            assert query['time_filter'] == {'mode': 'none', 'start': None, 'end': None}
            # Dense index has exactly one sampled chunk per article. The larger
            # ranking supplies prefixes for each pool; saved top-12 is replay-checked.
            lexical = search_single(BM25, query['query'], topic, 250)
            semantic = search_dense(DENSE, query['query'], topic, 250)
            base = fused_order(lexical, semantic, 60)
            base_ids = [str(d['id']) for d in base]
            expected = [str(i) for i in query['result_ids']]
            if base_ids[:12] != expected:
                mismatches.append({'query_number': number, 'expected': expected, 'actual': base_ids[:12]})
            for k in [12, 24, 50]:
                aggregate[f'fixed{k}'].update(base_ids[:k])
            aggregate['fixed_all'].update(base_ids)
            aggregate['candidate_union'].update(str(d['id']) for d in lexical[:60] + semantic[:60])
            aggregate['native24'].update(str(d['id']) for d in fused_order(lexical, semantic, 120)[:24])
            aggregate['native50'].update(str(d['id']) for d in fused_order(lexical, semantic, 250)[:50])
            for rank, document_id in enumerate(base_ids, 1):
                for day in dates_by_doc[document_id] & missing:
                    if day not in best_rank or rank < best_rank[day]['rank']:
                        best_rank[day] = {'rank': rank, 'query_number': number,
                                          'query': query['query'], 'document_id': document_id}
            replay.append({'query_number': number, 'query': query['query'], 'pool': 60,
                           'fused_document_ids': base_ids,
                           'bm25_ids': [str(d['id']) for d in lexical[:60]],
                           'dense_ids': [str(d['id']) for d in semantic[:60]]})
            if number % 10 == 0:
                print(json.dumps({'topic': topic, 'replayed': number, 'total': len(state['search_history'])}), flush=True)
        measures = {name: {'unique_articles': len(ids), 'retrieved_gold_date_hits': len(dates_for(ids) & gold),
                          'new_previously_unretrieved_gold_dates': sorted(dates_for(ids) & missing)}
                    for name, ids in aggregate.items()}
        all_candidates_dates = dates_for(aggregate['candidate_union'])
        full_order_dates = dates_for(aggregate['fixed_all'])
        partition = {
            'returned_after_top12_in_original_pool': sorted(missing & full_order_dates),
            'present_only_before_content_deduplication': sorted((missing & all_candidates_dates) - full_order_dates),
            'outside_original_bm25_and_dense_top60_pools': sorted(missing - all_candidates_dates),
        }
        probes = []
        for day, query in PROBES[topic]:
            lexical = search_single(BM25, query, topic, 60)
            semantic = search_dense(DENSE, query, topic, 60)
            order = fused_order(lexical, semantic, 60)
            hits = [{'rank': i, 'document_id': str(d['id'])} for i, d in enumerate(order, 1)
                    if day in dates_by_doc[str(d['id'])]]
            probes.append({'target_date': day, 'query': query, 'top12_target_date_hit': any(h['rank'] <= 12 for h in hits),
                           'matching_articles': hits, 'best_rank_under_original_queries': best_rank.get(day)})
        result = {'topic': topic, 'gold_available': len(gold), 'original_query_count': len(state['search_history']),
                  'time_filter_modes': dict(Counter(q['time_filter']['mode'] for q in state['search_history'])),
                  'original_final_hits': score['matched_gold_date_count'],
                  'original_retrieved_hits': score['retrieved_extracted_date_hits'],
                  'saved_top12_replay_mismatches': mismatches, 'measurements': measures,
                  'missing_date_partition': partition, 'best_missing_date_ranks': best_rank, 'diagnostic_probes': probes}
        (OUT / f'{topic}_query_replay.json').write_text(json.dumps(replay, indent=2) + '\n', encoding='utf-8')
        (OUT / f'{topic}_report.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        reports.append(result)
        print(json.dumps({'topic': topic, 'done': True, 'mismatches': len(mismatches),
                          'hits': {k: v['retrieved_gold_date_hits'] for k, v in measures.items()},
                          'partition': {k: len(v) for k, v in partition.items()},
                          'targeted_probes_hit': sum(p['top12_target_date_hit'] for p in probes)}), flush=True)
    report = {'api_calls': 0, 'query_policy_frozen': True, 'verify_rerun': False,
              'method': 'Replay existing queries; fixed60 uses original BM25/dense candidate pools and only extends returned rank prefix. native24/50 also expands each candidate pool to top_k * 5.',
              'probe_warning': 'Targeted probes are selected after inspecting missed Gold events. They diagnose retrievability, not autonomous policy performance or an unbiased benchmark.',
              'limitations': ['Retrieval date hits are not final timeline coverage; no VERIFY rerun.',
                              'Changing top_k online would change later state, queries and costs.',
                              'A target article outside top60 for existing queries may reflect query wording, lexical/dense representation or ranking; these are not fully separated.'],
              'topics': reports}
    (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
