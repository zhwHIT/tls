"""Evidence-bearing adjudication before different dates can be merged away."""
import copy
from chronos_repro.gold_supervision import text_similarity


def partition(client, state, candidates, config, trajectory, usage, phase):
    from run_tisa_two_phase_annotation import repaired_call, add_audits, model_step, LabelValidationError
    from chronos_repro.compact_context import ContextLimitError
    from batched_search_phase import _fits, API_SYSTEM
    if not config.get('date_conflict_review', {}).get('enabled'):
        return candidates, [], {}
    threshold = config['date_conflict_review'].get('similarity_threshold', .55)
    reader = state.get('_evidence_reader')
    available = getattr(reader, 'passages', {})
    pending, operations, fused = [], [], {}
    used_targets = set()
    for candidate in candidates:
        event = candidate['event']
        if any(e['time'] == event['time'] and e['summary'].strip().casefold() == event['summary'].strip().casefold()
               for e in state['timeline_events']):
            pending.append(candidate)
            continue
        matches = [e for e in state['timeline_events'] if e['time'] != event['time']
                   and text_similarity(e['summary'], event['summary']) >= threshold]
        if not matches:
            pending.append(candidate); continue
        existing = max(matches, key=lambda e: text_similarity(e['summary'], event['summary']))
        proofs = []
        for side, row in [('existing', existing), ('candidate', candidate)]:
            de = row.get('date_evidence') or {}
            doc = available.get(de.get('document_id'))
            quote = de.get('quote', '')
            if doc and quote and quote in doc['text']:
                offset = doc['text'].index(quote)
                proofs.append({'side': side, 'document_id': doc['id'], 'quote': quote,
                               'text': doc['text'][max(0, offset-180):offset+len(quote)+180],
                               'publication_date': doc.get('publication_date'), 'date_evidence': copy.deepcopy(de)})
        payload = {'stage': 'DATE_CONFLICT', 'existing': existing, 'candidate': candidate,
                   'evidence': proofs, 'instruction': 'Determine whether these are the same occurrence. '
                   'Compare occurrence, announcement and publication dates. Publication date alone is not event date. '
                   'Choose EXISTING or CANDIDATE only with evidence for that date; DIFFERENT for distinct occurrences; '
                   'DEFER when evidence is insufficient. Do not invent a third date.',
                   'required_json': {'decision': 'EXISTING|CANDIDATE|DIFFERENT|DEFER', 'reason': 'brief explanation',
                                     'support': [{'side': 'existing|candidate', 'quote': 'verbatim from supplied text'}]}}
        def validate(out):
            if out.get('decision') not in {'EXISTING','CANDIDATE','DIFFERENT','DEFER'}:
                raise ValueError('Unknown date decision')
            if not isinstance(out.get('reason'), str) or not out['reason'].strip():
                raise ValueError('Date decision requires a reason')
            if out['decision'] != 'DEFER':
                supports = out.get('support', [])
                for proof in supports:
                    if not proof.get('quote') or not any(p['side']==proof.get('side') and proof['quote'] in p['text'] for p in proofs):
                        raise ValueError('Date decision quote is not literal evidence')
                required = {'existing','candidate'} if out['decision']=='DIFFERENT' else {out['decision'].lower()}
                if not required <= {p.get('side') for p in supports}:
                    raise ValueError('Date decision lacks evidence for chosen side')
            return copy.deepcopy(out)
        if len(proofs) != 2 or existing['event_id'] in used_targets or not _fits(client, API_SYSTEM, payload):
            result = {'decision':'DEFER','reason':'Missing evidence, busy target or input budget; keep diagnostic candidate.', 'support':[]}
        else:
            try:
                result, audits = repaired_call(client, API_SYSTEM, payload, validate, config)
                add_audits(trajectory, usage, phase+':DATE_CONFLICT', audits)
            except (LabelValidationError, ContextLimitError) as error:
                add_audits(trajectory, usage, phase+':DATE_CONFLICT_FAILED', getattr(error, 'audits', []))
                result = {'decision':'DEFER','reason':'Date adjudication failed validation or input budget.',
                          'support':[], 'error':str(error), 'executor_deferred':True}
        model_step(trajectory, phase, 'DATE_CONFLICT', payload, result, {'training_target':False}, 'date_conflict_diagnostic')
        decision = result['decision']
        # Handle the pair here so ordinary MERGE cannot discard a DIFFERENT decision.
        op = {'candidate_id':candidate['candidate_id'], 'operation':'APPEND' if decision=='DIFFERENT' else 'DROP',
              'target_event_id':None, 'reason':result['reason']}
        if decision in {'EXISTING','CANDIDATE'}:
            chosen = copy.deepcopy(existing)
            if decision == 'CANDIDATE':
                chosen.update(copy.deepcopy(candidate['event']))
                chosen.update(confidence=candidate['confidence'], conflict=candidate['status']=='CONFLICTED',
                              date_evidence=copy.deepcopy(candidate['date_evidence']))
            chosen['evidence_ids'] = sorted(set(existing['evidence_ids']) | set(candidate['evidence_ids']))
            fused[candidate['candidate_id']] = chosen
            op.update(operation='UPDATE', target_event_id=existing['event_id'])
            used_targets.add(existing['event_id'])
        elif decision == 'DEFER':
            existing['conflict'] = True
            state.setdefault('date_conflict_quarantine', []).append({'candidate':copy.deepcopy(candidate),
                'existing_event_id':existing['event_id'], 'decision':result})
        operations.append(op)
    return pending, operations, fused
