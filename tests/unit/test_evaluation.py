from pathlib import Path
from typing import Any

import pytest

from steam_research.evaluation import batch_contamination, evaluate, load_gold_set
from steam_research.stage1 import validate_results
from steam_research.taxonomy import load_merged_taxonomy

GOLD_PATH = Path("tests/unit/fixtures/gold/stage1_gold_v1.json")
TAXONOMY = load_merged_taxonomy(
    Path("config/taxonomies/universal-core.yaml"),
    Path("config/taxonomies/desktop-mascot.yaml"),
)


def output_for(gold: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "input_id": record.input_id,
            "review_id": record.review_id,
            "is_actionable": record.actionable,
            "engagement_posture": record.engagement_posture,
            "overall_sentiment": record.overall_sentiment,
            "sentiment_intensity": 3,
            "aspects": [
                {
                    "taxonomy_id": aspect,
                    "sentiment": "mixed",
                    "intensity": 3,
                    "evidence": record.review_text[:8],
                    "feature_request": None,
                    "confidence": 0.8,
                }
                for aspect in record.aspects
            ],
            "use_cases": [],
            "self_reported_abandonment": {
                "mentioned": False,
                "statement_type": None,
                "evidence": None,
                "verified": False,
            },
            "summary": "Synthetic evaluation result.",
        }
        for record in gold
        if record.eligible
    ]


def test_gold_fixture_loads_and_covers_required_dimensions() -> None:
    records = load_gold_set(GOLD_PATH)
    assert {record.language for record in records} >= {
        "english",
        "german",
        "french",
        "spanish",
        "japanese",
        "korean",
    }
    assert any(record.playtime_at_review < 60 for record in records)
    assert any(record.playtime_at_review >= 600 for record in records)
    assert any(not record.eligible for record in records)
    assert any(len(record.aspects) > 1 for record in records)


def test_perfect_gold_output_scores_one() -> None:
    records = load_gold_set(GOLD_PATH)
    metrics = evaluate(
        records,
        output_for(records),
        taxonomy_ids=TAXONOMY.ids,
    )
    assert metrics.schema_validity == 1.0
    assert metrics.coverage == 1.0
    assert metrics.omission_rate == 0.0
    assert metrics.actionability_accuracy == 1.0
    assert metrics.category_precision == 1.0
    assert metrics.category_recall == 1.0
    assert metrics.sentiment_agreement == 1.0
    assert metrics.posture_agreement == 1.0
    assert metrics.batch_contamination == 0


def test_missing_and_duplicate_outputs_are_reported() -> None:
    records = load_gold_set(GOLD_PATH)
    output = output_for(records)
    output.pop()
    output.append(output[0])
    metrics = evaluate(records, output, taxonomy_ids=TAXONOMY.ids)
    assert metrics.schema_validity == 0.0
    assert metrics.omission_rate > 0.0
    assert metrics.batch_contamination == 1


def test_cross_batch_and_unknown_ids_are_contamination() -> None:
    records = load_gold_set(GOLD_PATH)
    expected = frozenset(record.input_id for record in records if record.eligible)
    raw = [{"input_id": "other-batch"}, {"input_id": next(iter(expected))}]
    assert batch_contamination(raw, expected) == 1
    assert batch_contamination(raw + raw[1:], expected) == 2


def test_real_validator_rejects_missing_ids() -> None:
    records = load_gold_set(GOLD_PATH)
    eligible = [record for record in records if record.eligible]
    with pytest.raises(ValueError, match="missing"):
        validate_results(
            output_for(records)[:-1],
            input_ids={record.input_id for record in eligible},
            review_text_by_input={
                record.input_id: record.review_text for record in eligible
            },
            taxonomy_ids=TAXONOMY.ids,
        )
