import pytest

from chronos_repro.multievent_qrels import validate_multievent_judgments


def test_validate_multievent_matrix_and_flatten():
    rows = validate_multievent_judgments(
        {
            "documents": [
                {
                    "id": "d1",
                    "grades": {"e1": 2, "e2": 0},
                    "positive_details": [
                        {
                            "event_id": "e1",
                            "grade": 2,
                            "event_date": "2011-01-01",
                            "reason": "Directly reports the event.",
                        }
                    ],
                },
                {
                    "id": "d2",
                    "grades": {"e2": 0},
                    "positive_details": [],
                },
            ]
        },
        {"d1": ["e1", "e2"], "d2": ["e2"]},
    )
    assert [(row["id"], row["event_id"], row["grade"]) for row in rows] == [
        ("d1", "e1", 2),
        ("d1", "e2", 0),
        ("d2", "e2", 0),
    ]


def test_validate_multievent_requires_details_for_positive_grade():
    with pytest.raises(ValueError, match="positive_details"):
        validate_multievent_judgments(
            {
                "documents": [
                    {
                        "id": "d1",
                        "grades": {"e1": 1},
                        "positive_details": [],
                    }
                ]
            },
            {"d1": ["e1"]},
        )
