'''Atomic gap contracts. Structural evidence checks do not prove entailment.'''
from __future__ import annotations

import copy
import re


TARGET_SCHEMA = {
    'question': 'one factual question, 6-180 characters',
    'anchor_quote': '6-160 character verbatim quote from an anchor event summary',
    'completion_criterion': 'one observable evidence condition, 6-180 characters',
    'seed_query': 'focused English query, 3-20 tokens, at most 160 characters',
}
GAP_PROMPT = (
    'Create atomic evidence-seeking tasks, not broad research agendas. Each gap must '
    'address ONE factual uncertainty grounded in at least one visible anchor event. '
    'Provide retrieval_target with question, anchor_quote, completion_criterion and '
    'seed_query. Quote the anchor summary verbatim; do not invent the missing answer. '
    'A long interval or two adjacent dates alone does not establish missing events '
    'or a causal relation. Do not request all developments, all causes, the full '
    'history or general background. Split independent questions into separate tasks. '
    'A causal task requires a concrete causal clue in its anchor, not merely chronology. '
    'Year/month-level milestones are already known at that precision, not absent. '
    'Return no gaps if no evidence-grounded uncertainty can be identified. '
    'completion_criterion states the evidence needed for this single question. '
    'seed_query is a proposal, never an invented executed search.'
)
GAP_FEW_SHOTS = [
    {'anchor_summary': 'Aurora signed a purchase agreement subject to regulatory approval.',
     'broad_rejected': 'Explain the entire transaction history and all economic causes.',
     'retrieval_target': {
         'question': 'Was the regulatory approval required by the Aurora agreement granted?',
         'anchor_quote': 'subject to regulatory approval',
         'completion_criterion': 'A sourced report states whether the required approval was granted.',
         'seed_query': 'Aurora purchase agreement regulatory approval decision'}},
    {'anchor_summary': 'The artist released an album in 1996.',
     'decision': 'The year-level milestone is known. Do not invent a missing daily event or a causal gap merely because the day is unknown.'},
]
_BROAD = re.compile(
    r'\b(?:all (?:events|developments|causes|factors)|(?:complete|entire|full) '
    r'(?:history|timeline|biography)|overall (?:history|progression)|general background|'
    r'underlying (?:drivers|causes)|broader context)\b'
    r'|所有(?:事件|原因|因素)|完整(?:历史|时间线)|整体背景|全部经过', re.I)
_VACUOUS = re.compile(r'\bgap (?:is |has been )?(?:filled|closed|resolved)\b|'
                      r'\b(?:sufficient context|enough information|complete understanding)\b|'
                      r'信息充足|上下文完整|缺口已(?:填补|解决)', re.I)


def _text(value, field, maximum):
    if not isinstance(value, str) or not 6 <= len(value.strip()) <= maximum:
        raise ValueError(f'{field} must be text of 6-{maximum} characters')
    return value.strip()


def gap_signature(gap):
    '''Different factual questions at the same anchors remain separate tasks.'''
    anchors = tuple(sorted({str(gap[k]) for k in ('left_event_id', 'right_event_id')
                            if gap.get(k) is not None}))
    question = ' '.join(re.findall(r'\w+', gap.get('retrieval_target', {}).get('question', '').casefold()))
    return (gap.get('type'), anchors, question)


def validate_atomic_gap(gap, events, *, required=False):
    result = copy.deepcopy(gap)
    target = result.get('retrieval_target')
    if target is None and not required:
        return result
    if not isinstance(target, dict) or set(target) != set(TARGET_SCHEMA):
        raise ValueError('atomic gap requires retrieval_target with question, anchor_quote, completion_criterion, seed_query')
    by_id = {str(e['event_id']): e for e in events}
    anchors = {str(result[k]) for k in ('left_event_id', 'right_event_id') if result.get(k) is not None}
    if not anchors or not anchors <= set(by_id):
        raise ValueError('atomic gap requires at least one existing anchor event')
    clean = {k: _text(target[k], k, 160 if k in {'anchor_quote', 'seed_query'} else 180)
             for k in TARGET_SCHEMA}
    if not any(clean['anchor_quote'] in str(by_id[a].get('summary', '')) for a in anchors):
        raise ValueError('anchor_quote must occur verbatim in an anchor event summary')
    if any(_BROAD.search(clean[k]) for k in ('question', 'completion_criterion', 'seed_query')):
        raise ValueError('gap is too broad; request one specific fact, not an exhaustive history or all causes')
    if _VACUOUS.search(clean['completion_criterion']):
        raise ValueError('completion_criterion must name an observable fact, not merely say the gap is resolved')
    if clean['question'].count('?') + clean['question'].count('？') > 1:
        raise ValueError('split multiple gap questions into separate atomic tasks')
    if not 3 <= len(re.findall(r'[A-Za-z0-9]+', clean['seed_query'])) <= 20:
        raise ValueError('seed_query must contain 3-20 English query tokens')
    result['retrieval_target'] = clean
    return result


def validate_resolution(raw, active_gap, events, *, required=True):
    resolved = raw.get('resolved_gap_ids', [])
    if not isinstance(resolved, list) or any(not isinstance(i, str) for i in resolved):
        raise ValueError('resolved_gap_ids must be a list of strings')
    if len(resolved) != len(set(resolved)) or set(resolved) - {active_gap['gap_id']}:
        raise ValueError('local gap update may resolve only the active gap once')
    proofs = raw.get('resolution_evidence', [])
    if not isinstance(proofs, list):
        raise ValueError('resolution_evidence must be a list')
    if not required and not proofs:
        return []
    by_id = {str(e['event_id']): e for e in events}
    seen = set()
    for proof in proofs:
        if not isinstance(proof, dict) or set(proof) != {'gap_id', 'event_id', 'quote'}:
            raise ValueError('resolution evidence requires gap_id, event_id, quote')
        if not isinstance(proof['gap_id'], str) or not isinstance(proof['event_id'], str):
            raise ValueError('resolution IDs must be strings')
        if proof['gap_id'] not in resolved or proof['event_id'] not in by_id:
            raise ValueError('resolution evidence must reference the resolved gap and a current event')
        quote = _text(proof['quote'], 'resolution quote', 180)
        if quote not in str(by_id[proof['event_id']].get('summary', '')):
            raise ValueError('resolution quote must occur verbatim in the cited event')
        if by_id[proof['event_id']].get('conflict'):
            raise ValueError('a conflicted event cannot establish gap resolution')
        seen.add(proof['gap_id'])
    if seen != set(resolved):
        raise ValueError('each resolved gap requires event-grounded resolution_evidence')
    return copy.deepcopy(proofs)
