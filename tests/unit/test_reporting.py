import pytest

from steam_research.reporting import ReportLimits, render_markdown
from steam_research.stage2 import (
    EvidenceReference,
    MetricReference,
    Stage2EvidencePayload,
    Stage2Output,
)


def contract() -> Stage2EvidencePayload:
    return Stage2EvidencePayload(
        project_id="p",
        aggregate_run_id="a",
        metrics=(MetricReference("m", "rate", 1, 2, 0.5, "eligible", {}),),
        evidence=(
            EvidenceReference(
                "s", "r", 10, "english", "bounded quote", "confirming", "h"
            ),
        ),
    )


def test_renderer_is_stable_for_no_evidence() -> None:
    payload = Stage2EvidencePayload("p", "a", (), ())
    output = Stage2Output(
        "insufficient_evidence", (), (), (), ("No eligible evidence was selected.",)
    )
    rendered = render_markdown(
        output, payload, limits=ReportLimits(min_words=1, max_words=300)
    )
    assert rendered == render_markdown(
        output, payload, limits=ReportLimits(min_words=1, max_words=300)
    )
    assert "No ranked priorities" in rendered


def test_renderer_includes_lineage_for_one_competitor() -> None:
    output = Stage2Output(
        "complete",
        ("A clear onboarding gap",),
        (),
        (),
        ("Coverage is limited to selected evidence.",),
        ({"name": "Solo", "signal": "Needs onboarding focus"},),
    )
    rendered = render_markdown(
        output, contract(), limits=ReportLimits(min_words=1, max_words=300)
    )
    assert "Solo" in rendered
    assert "Competitor comparison" in rendered


def test_renderer_handles_multiple_competitors_and_rejects_word_overflow() -> None:
    output = Stage2Output(
        "complete",
        ("Direction",),
        (),
        ("Counterevidence remains limited.",),
        ("Two competitors were compared.",),
        ({"name": "A", "signal": "Strong"}, {"name": "B", "signal": "Weak"}),
        ("Reliable onboarding is expected.",),
        ("Setup friction is exposed.",),
        ("Lead with a clear promise.",),
        ("Use proof-led store imagery.",),
    )
    rendered = render_markdown(
        output, contract(), limits=ReportLimits(min_words=1, max_words=300)
    )
    assert "| A | Strong |" in rendered
    assert "Reliable onboarding is expected." in rendered
    assert "Use proof-led store imagery." in rendered
    assert "| B | Weak |" in rendered
    with pytest.raises(ValueError, match="words"):
        render_markdown(
            output, contract(), limits=ReportLimits(min_words=1, max_words=2)
        )
