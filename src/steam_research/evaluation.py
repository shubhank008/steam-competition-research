"""Deterministic offline evaluation for the Stage 1 gold set."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steam_research.stage1 import Stage1ValidationError, validate_results


@dataclass(frozen=True)
class GoldRecord:
    input_id: str
    review_id: str
    language: str
    voted_up: bool
    playtime_at_review: int
    review_text: str
    eligible: bool
    actionable: bool
    overall_sentiment: str
    engagement_posture: str
    aspects: frozenset[str]


@dataclass(frozen=True)
class EvaluationMetrics:
    schema_validity: float
    coverage: float
    omission_rate: float
    actionability_accuracy: float
    category_precision: float
    category_recall: float
    sentiment_agreement: float
    posture_agreement: float
    batch_contamination: int


def load_gold_set(path: Path) -> tuple[GoldRecord, ...]:
    """Load and validate the committed JSON gold-set fixture."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != "stage1-gold-v1":
        raise ValueError("unsupported gold-set version")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("gold set must contain records")
    result: list[GoldRecord] = []
    seen: set[str] = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("gold record must be an object")
        annotation = raw.get("annotation")
        if not isinstance(annotation, dict):
            raise ValueError("gold record annotation must be an object")
        input_id = _string(raw, "input_id")
        if input_id in seen:
            raise ValueError(f"duplicate gold input_id: {input_id}")
        seen.add(input_id)
        aspects = annotation.get("aspects")
        if not isinstance(aspects, list) or not all(
            isinstance(value, str) for value in aspects
        ):
            raise ValueError("gold aspects must be a list of strings")
        result.append(
            GoldRecord(
                input_id,
                _string(raw, "review_id"),
                _string(raw, "language"),
                _bool(raw, "voted_up"),
                _int(raw, "playtime_at_review"),
                _string(raw, "review_text"),
                _bool(raw, "eligible"),
                _bool(annotation, "actionable"),
                _string(annotation, "overall_sentiment"),
                _string(annotation, "engagement_posture"),
                frozenset(aspects),
            )
        )
    return tuple(result)


def evaluate(
    gold: tuple[GoldRecord, ...],
    raw_output: Any,
    *,
    taxonomy_ids: Collection[str],
    batch_input_ids: Collection[str] | None = None,
) -> EvaluationMetrics:
    """Validate and score one batch; invalid output produces zero validity."""
    eligible = tuple(record for record in gold if record.eligible)
    expected_ids = {record.input_id for record in eligible}
    texts = {record.input_id: record.review_text for record in eligible}
    contamination = batch_contamination(raw_output, batch_input_ids or expected_ids)
    try:
        results = validate_results(
            raw_output,
            input_ids=expected_ids,
            review_text_by_input=texts,
            taxonomy_ids=taxonomy_ids,
        )
    except (Stage1ValidationError, KeyError, TypeError):
        omission = _omission(raw_output, expected_ids)
        return EvaluationMetrics(
            0.0,
            1.0 - omission,
            omission,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            contamination,
        )
    by_gold = {record.input_id: record for record in eligible}
    predicted_categories = [
        {aspect.taxonomy_id for aspect in result.aspects} for result in results
    ]
    gold_categories = [set(by_gold[result.input_id].aspects) for result in results]
    predicted_total = sum(len(value) for value in predicted_categories)
    gold_total = sum(len(value) for value in gold_categories)
    matched = sum(
        len(predicted & expected)
        for predicted, expected in zip(
            predicted_categories, gold_categories, strict=True
        )
    )
    return EvaluationMetrics(
        1.0,
        1.0,
        0.0,
        _mean(
            result.is_actionable == by_gold[result.input_id].actionable
            for result in results
        ),
        matched / predicted_total if predicted_total else 1.0,
        matched / gold_total if gold_total else 1.0,
        _mean(
            result.overall_sentiment == by_gold[result.input_id].overall_sentiment
            for result in results
        ),
        _mean(
            result.engagement_posture == by_gold[result.input_id].engagement_posture
            for result in results
        ),
        contamination,
    )


def batch_contamination(raw_output: Any, expected_ids: Collection[str]) -> int:
    """Count duplicate, unknown, and cross-batch result IDs in raw output."""
    if not isinstance(raw_output, list):
        return 1
    ids = [item.get("input_id") for item in raw_output if isinstance(item, dict)]
    return (
        sum(
            1
            for input_id in ids
            if not isinstance(input_id, str) or input_id not in expected_ids
        )
        + len(ids)
        - len(set(ids))
    )


def _omission(raw_output: Any, expected_ids: set[str]) -> float:
    if not expected_ids:
        return 0.0
    if not isinstance(raw_output, list):
        return 1.0
    actual = {item.get("input_id") for item in raw_output if isinstance(item, dict)}
    return len(expected_ids - actual) / len(expected_ids)


def _mean(values: Any) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 1.0


def _string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _bool(value: dict[str, Any], key: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        raise ValueError(f"{key} must be boolean")
    return result


def _int(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ValueError(f"{key} must be an integer")
    return result
