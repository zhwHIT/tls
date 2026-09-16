"""Bound runtime defaults for the temporal-grounding diagnostic revision."""
RUNTIME_REVISION = 'two-phase-v9.5-state-continuation-and-gap-contract'
REQUEST_OPTIONS = {'thinking': {'type': 'disabled'}, 'max_tokens': 8192}
RUNTIME_OPTIONS = {'request_options': REQUEST_OPTIONS, 'frozen_timex': True,
                   'max_temporal_annotations_per_passage': 12, 'candidate_batch_cap': 12,
                   'policy_pending_leads': 6, 'private_gold_visible': False,
                   'coarse_extraction_pages': 1, 'gap_extraction_pages_max': 2,
                   'structured_timeline_year_context': True, 'source_quote_chars_max': 180,
                   'post_merge_observation_extraction': False, 'phase1_coarse_priority': True,
                   'phase2_passages_per_search': 4, 'policy_stall_handoff': 'incomplete_not_training_target',
                   'gap_update_exact_duplicate_records_removed': True,
                   'atomic_gap_contract_default': True, 'phase2_max_attempts_per_gap_default': 3}
