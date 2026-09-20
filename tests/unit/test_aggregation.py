from steam_research.aggregation import Metric, _cohort


def test_zero_denominator_is_unknown_not_zero() -> None:
    metric = Metric("m", "rate", {}, 0, 0, "empty", {})
    assert metric.value is None


def test_playtime_cohort_boundaries_are_stable() -> None:
    assert [_cohort(value) for value in (None, 0, 59, 60, 599, 600)] == [
        "unknown",
        "early",
        "early",
        "established",
        "established",
        "long",
    ]
