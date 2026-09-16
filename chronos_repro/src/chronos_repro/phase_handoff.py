"""Guarded transfer of an established but incomplete skeleton after policy stalling."""


def can_handoff_repeated_query(error, memory, events, rounds, config):
    return (
        'query repeats an earlier search' in str(error)
        and rounds >= config['phase1_min_search_rounds']
        and len(events) >= config['phase1_min_events']
        and len(memory.get('stage_outline', [])) >= config.get('exploration_memory', {}).get('minimum_stages', 3)
        and {'EARLIER', 'LATER'}.issubset({r.get('strategy') for r in memory.get('search_history', [])})
    )
