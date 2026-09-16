"""Strict date aggregation without silently dropping failed/missing topics."""
from __future__ import annotations

import math


def summarize_topics(rows, expected_topics, target=0.7):
    if not 0 < target <= 1:
        raise ValueError('target must be in (0, 1]')
    expected = [tuple(x) for x in expected_topics]
    if len(set(expected)) != len(expected) or len(expected) < 2:
        raise ValueError('Evaluation needs at least two distinct dataset/topic pairs')
    keyed = {}
    for row in rows:
        key = (row['dataset'], row['topic'])
        if key in keyed or key not in expected:
            raise ValueError('Duplicate or unplanned evaluation topic')
        keyed[key] = row
    reports, completed = [], []
    for dataset, topic in expected:
        row = keyed.get((dataset, topic))
        if row is None:
            reports.append({'dataset': dataset, 'topic': topic, 'status': 'missing'})
            continue
        h, g, p = (row[k] for k in ('gold_date_hits', 'gold_date_count', 'predicted_date_count'))
        if any(type(v) is not int or v < 0 for v in (h, g, p)) or not 0 <= h <= min(g, p) or not g:
            raise ValueError('Invalid strict date counts')
        recall, precision = h / g, h / p if p else 0.0
        report = {**row, 'gold_date_recall': recall, 'date_precision': precision,
                  'date_f1': 2 * h / (g + p), 'minimum_date_hits': math.ceil(target * g),
                  'target_reached': recall >= target}
        reports.append(report)
        if row['status'] == 'ok':
            completed.append(report)
    all_complete = len(completed) == len(expected)
    # Partial results stay visible, but do not become the suite headline.
    aggregate = None
    if all_complete:
        hits = sum(r['gold_date_hits'] for r in completed)
        gold = sum(r['gold_date_count'] for r in completed)
        pred = sum(r['predicted_date_count'] for r in completed)
        aggregate = {'macro_gold_date_recall': sum(r['gold_date_recall'] for r in completed) / len(completed),
                     'micro_gold_date_recall': hits / gold,
                     'micro_date_precision': hits / pred if pred else 0.0,
                     'micro_date_f1': 2 * hits / (gold + pred),
                     'topics_meeting_target': sum(r['target_reached'] for r in completed),
                     'all_topics_meet_target': all(r['target_reached'] for r in completed)}
    return {'metric': 'exact_date_intersection_reference_union', 'target': target,
            'expected_topic_count': len(expected), 'completed_topic_count': len(completed),
            'all_complete': all_complete, 'aggregate': aggregate, 'topics': reports,
            'semantic_event_coverage': 'not measured by date overlap'}
