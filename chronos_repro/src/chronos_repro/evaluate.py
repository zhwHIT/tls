from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable


@dataclass(frozen=True)
class PRF:
    precision: float
    recall: float
    f_score: float


def _prf(shared: int, predicted: int, reference: int) -> PRF:
    precision = shared / predicted if predicted else 0.0
    recall = shared / reference if reference else 0.0
    f_score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return PRF(precision, recall, f_score)


def evaluate_dates(
    prediction: dict[date, tuple[str, ...]], references: Iterable[dict[date, tuple[str, ...]]]
) -> PRF:
    reference_dates: set[date] = set()
    for reference in references:
        reference_dates.update(reference)
    predicted_dates = set(prediction)
    return _prf(len(predicted_dates & reference_dates), len(predicted_dates), len(reference_dates))


def evaluate_tilse(
    prediction: dict[date, tuple[str, ...]],
    references: Iterable[dict[date, tuple[str, ...]]],
    rouge_backend: str = "original",
) -> dict:
    """Run the same five Timeline ROUGE variants used by CHRONOS."""
    try:
        from tilse.data.timelines import GroundTruth, Timeline
        from tilse.evaluation.rouge import TimelineRougeEvaluator
    except ImportError as error:
        raise RuntimeError(
            "Timeline ROUGE requires the optional dependency: pip install -e '.[eval]'"
        ) from error

    refs = tuple(references)
    if rouge_backend not in {"original", "reimpl"}:
        raise ValueError(f"Unsupported ROUGE backend: {rouge_backend}")
    evaluator = TimelineRougeEvaluator(
        measures=["rouge_1", "rouge_2"], rouge_computation=rouge_backend
    )
    pred = Timeline({key: list(value) for key, value in prediction.items()})
    ground_truth = GroundTruth(
        [Timeline({key: list(value) for key, value in ref.items()}) for ref in refs]
    )
    rouge = {
        "concat": evaluator.evaluate_concat(pred, ground_truth),
        "agreement": evaluator.evaluate_agreement(pred, ground_truth),
        "align_date_costs": evaluator.evaluate_align_date_costs(pred, ground_truth),
        "align_date_content_costs": evaluator.evaluate_align_date_content_costs(pred, ground_truth),
        "align_date_content_costs_many_to_one": (
            evaluator.evaluate_align_date_content_costs_many_to_one(pred, ground_truth)
        ),
    }
    return {
        "date_score": asdict(evaluate_dates(prediction, refs)),
        "rouge_backend": rouge_backend,
        "rouge": rouge,
    }
