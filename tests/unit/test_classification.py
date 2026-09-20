import pytest

from steam_research.classification import (
    BatchLimits,
    ClassificationInput,
    estimate_tokens,
    plan_batches,
    select_inputs,
)


def item(
    number: int, *, appid: int = 10, language: str = "english", positive: bool = True
) -> ClassificationInput:
    return ClassificationInput(
        "project",
        appid,
        f"r{number}",
        f"hash-{number}",
        "policy",
        language,
        positive,
        number,
        number * 60,
        1_700_000_000 + number,
        1_700_000_000 + number,
        f"review text {number}",
    )


def test_token_planner_respects_item_and_token_limits() -> None:
    values = [item(index) for index in range(5)]
    batches = plan_batches(values, BatchLimits(max_items=2, max_tokens=100))
    assert all(
        len(batch.inputs) <= 2 and batch.estimated_tokens <= 100 for batch in batches
    )
    assert {value.input_id for batch in batches for value in batch.inputs} == {
        value.input_id for value in values
    }


def test_oversized_input_is_explicitly_rejected() -> None:
    value = item(1)
    oversized = ClassificationInput(**{**value.__dict__, "review_text": "x" * 1000})
    with pytest.raises(ValueError, match="exceeds token limit"):
        plan_batches([oversized], BatchLimits(max_tokens=10))


def test_scope_selection_is_stable_and_covers_strata() -> None:
    values = [
        item(
            index,
            appid=10 + index % 2,
            language="english" if index % 2 else "german",
            positive=index % 3 != 0,
        )
        for index in range(24)
    ]
    first = select_inputs(values, scope="stratified", seed=42, ceiling=8)
    second = select_inputs(values, scope="stratified", seed=42, ceiling=8)
    assert [value.input_id for value in first] == [value.input_id for value in second]
    assert {value.appid for value in first} == {10, 11}
    assert {value.language for value in first} == {"english", "german"}
    assert {value.voted_up for value in first} == {True, False}
    assert all(value.sampling_weight == pytest.approx(3) for value in first)


def test_unclassified_only_excludes_successful_marker() -> None:
    values = [
        item(1),
        ClassificationInput(**{**item(2).__dict__, "inclusion_reason": "classified"}),
    ]
    selected = select_inputs(values, scope="unclassified-only", seed=1)
    assert [value.recommendationid for value in selected] == ["r1"]
    assert estimate_tokens(values[0].projection()) > 0
