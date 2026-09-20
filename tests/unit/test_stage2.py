import pytest

from steam_research.stage2 import (
    EvidenceReference,
    MetricReference,
    Recommendation,
    Stage2EvidencePayload,
    Stage2Limits,
    Stage2Output,
    Stage2ValidationError,
    stage2_json_schema,
)


def payload() -> Stage2EvidencePayload:
    return Stage2EvidencePayload(
        project_id="p",
        aggregate_run_id="a",
        metrics=(
            MetricReference(
                "m",
                "aspect_rate",
                2,
                4,
                0.5,
                "classified reviews",
                {"language": "english"},
            ),
        ),
        evidence=(
            EvidenceReference(
                "s", "r", 10, "english", "A bounded quote", "confirming", "hash"
            ),
        ),
    )


def test_stage2_validates_recommendation_references_and_required_fields() -> None:
    contract = payload()
    recommendation = Recommendation(
        1,
        "Improve settings",
        "Expose settings",
        "high",
        "medium",
        ("m",),
        ("r",),
        "Run a usability test",
        "high",
    )
    output = Stage2Output(
        "complete", ("Settings are a gap",), (recommendation,), (), ()
    )
    output.validate(contract)
    assert output.to_dict()["schema_version"] == "stage2-v1"


def test_stage2_rejects_missing_denominator_and_unknown_metric() -> None:
    with pytest.raises(Stage2ValidationError, match="positive denominator"):
        MetricReference("m", "rate", 0, 0, None, "population", {})
    contract = payload()
    invalid = Recommendation(
        1, "Fix", "Do it", "low", "unknown", ("missing",), (), "Measure it", "low"
    )
    with pytest.raises(Stage2ValidationError, match="unknown evidence"):
        invalid.validate({metric.metric_id for metric in contract.metrics}, set())


def test_stage2_limits_and_insufficient_evidence_are_explicit() -> None:
    limited = Stage2Limits(max_evidence=0)
    with pytest.raises(Stage2ValidationError, match="limits"):
        Stage2EvidencePayload("p", "a", (), (payload().evidence[0],), limits=limited)
    insufficient = Stage2Output(
        "insufficient_evidence", (), (), (), ("Only one language was covered",)
    )
    insufficient.validate(payload())
    assert (
        stage2_json_schema(Stage2Limits(max_priorities=2))["properties"][
            "recommendations"
        ]["maxItems"]
        == 2
    )


def test_stage2_rejects_geography_claims() -> None:
    output = Stage2Output("complete", ("Users in France prefer this",), (), (), ())
    with pytest.raises(Stage2ValidationError, match="geography"):
        output.validate(payload())
