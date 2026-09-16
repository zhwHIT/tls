from __future__ import annotations

import re


def validate_multievent_judgments(
    payload: dict,
    expected: dict[str, list[str]],
) -> list[dict]:
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise ValueError("Response must contain a documents list")
    by_id = {}
    for row in documents:
        if not isinstance(row, dict):
            raise ValueError("Each document judgment must be an object")
        document_id = str(row.get("id", ""))
        if document_id not in expected or document_id in by_id:
            raise ValueError(f"Unexpected or duplicate document id: {document_id}")
        grades = row.get("grades")
        if not isinstance(grades, dict):
            raise ValueError(f"grades must be an object for {document_id}")
        normalized_grades = {str(key): value for key, value in grades.items()}
        if set(normalized_grades) != set(expected[document_id]):
            raise ValueError(f"grades keys do not match requested events for {document_id}")
        for event_id, grade in normalized_grades.items():
            if not isinstance(grade, int) or grade not in {0, 1, 2}:
                raise ValueError(f"Invalid grade for {document_id}:{event_id}")

        details = row.get("positive_details", [])
        if not isinstance(details, list):
            raise ValueError(f"positive_details must be a list for {document_id}")
        detail_by_event = {}
        for detail in details:
            if not isinstance(detail, dict):
                raise ValueError("Each positive detail must be an object")
            event_id = str(detail.get("event_id", ""))
            if event_id in detail_by_event or event_id not in normalized_grades:
                raise ValueError(f"Unexpected positive detail for {document_id}:{event_id}")
            grade = detail.get("grade")
            if grade not in {1, 2} or grade != normalized_grades[event_id]:
                raise ValueError(f"Positive detail grade mismatch for {document_id}:{event_id}")
            event_date = detail.get("event_date")
            if event_date is not None and not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", str(event_date)
            ):
                raise ValueError(f"Invalid event_date for {document_id}:{event_id}")
            reason = str(detail.get("reason", "")).strip()
            if not reason:
                raise ValueError(f"Missing positive reason for {document_id}:{event_id}")
            detail_by_event[event_id] = {
                "event_date": event_date,
                "reason": reason,
            }

        positive_events = {
            event_id
            for event_id, grade in normalized_grades.items()
            if grade > 0
        }
        if set(detail_by_event) != positive_events:
            raise ValueError(f"positive_details do not match nonzero grades for {document_id}")
        by_id[document_id] = (normalized_grades, detail_by_event)

    if set(by_id) != set(expected):
        missing = sorted(set(expected) - set(by_id))
        raise ValueError(f"Missing document judgments: {missing}")

    flattened = []
    for document_id, event_ids in expected.items():
        grades, details = by_id[document_id]
        for event_id in event_ids:
            detail = details.get(event_id)
            flattened.append(
                {
                    "id": document_id,
                    "event_id": event_id,
                    "grade": grades[event_id],
                    "event_date": detail["event_date"] if detail else None,
                    "reason": (
                        detail["reason"]
                        if detail
                        else "No positive match in the multi-event judgment matrix."
                    ),
                }
            )
    return flattened
