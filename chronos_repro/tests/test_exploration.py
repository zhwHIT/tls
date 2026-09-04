from datetime import date

from chronos_repro.exploration import date_gaps, query_similarity


def test_date_gaps_are_largest_first():
    gaps = date_gaps(
        date(2020, 1, 1), date(2020, 4, 1),
        {date(2020, 1, 10), date(2020, 3, 20)}, minimum_days=5,
    )
    assert gaps[0]["start"] == "2020-01-10"
    assert gaps[0]["end"] == "2020-03-20"


def test_query_similarity_uses_token_jaccard():
    assert query_similarity("Egypt crisis 2011", "Egypt crisis 2011 events") == 0.75
    assert query_similarity("Egypt 2011", "Syria 2013") == 0.0
