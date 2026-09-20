"""Deterministic Markdown rendering for validated Stage 2 output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from steam_research.stage2 import Stage2EvidencePayload, Stage2Output


@dataclass(frozen=True)
class ReportLimits:
    min_words: int = 1500
    max_words: int = 3000

    def __post_init__(self) -> None:
        if self.min_words < 1 or self.max_words < self.min_words:
            raise ValueError("report word limits are invalid")


def _words(markdown: str) -> int:
    return len(re.findall(r"\b[\w][\w'-]*\b", markdown))


def _bullet(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- None reported."


def render_markdown(
    output: Stage2Output,
    payload: Stage2EvidencePayload,
    *,
    limits: ReportLimits | None = None,
) -> str:
    """Render validated structured output without model calls or truncation."""
    output.validate(payload)
    limits = limits or ReportLimits()
    metrics = {item.metric_id: item for item in payload.metrics}
    evidence = {item.review_id: item for item in payload.evidence}
    lines = [
        "# Competitive strategy brief",
        "",
        f"**Status:** {output.status}",
        "",
        "## Executive direction",
        _bullet(output.executive_direction),
        "",
        "## Competitor comparison",
    ]
    if output.competitor_grid:
        lines.extend(["| Competitor | Signal |", "| --- | --- |"])
        for item in output.competitor_grid:
            name = str(item.get("name", item.get("appid", "Unknown competitor")))
            signal = str(item.get("signal", item.get("summary", "No signal supplied")))
            lines.append(f"| {name} | {signal} |")
    else:
        lines.append("No competitor comparison was supplied.")
    lines.extend(
        [
            "",
            "## Market expectations",
            _bullet(output.market_expectations),
            "",
            "## Competitor vulnerabilities",
            _bullet(output.vulnerabilities),
            "",
            "## Positioning and store strategy",
            _bullet(output.positioning + output.store_recommendations),
            "",
            "## Ranked priorities",
        ]
    )
    if output.recommendations:
        for recommendation in sorted(
            output.recommendations, key=lambda item: item.rank
        ):
            refs = []
            for metric_id in recommendation.evidence_metric_ids:
                metric = metrics[metric_id]
                refs.append(
                    f"`metric:{metric.metric_id}` "
                    f"({metric.numerator}/{metric.denominator})"
                )
            for review_id in recommendation.evidence_review_ids:
                evidence_item = evidence[review_id]
                refs.append(
                    f"`review:{evidence_item.review_id}` "
                    f"(`selection:{evidence_item.selection_id}`)"
                )
            citation = "; ".join(refs) or "No direct citation"
            lines.extend(
                [
                    f"### {recommendation.rank}. {recommendation.title}",
                    f"**Action:** {recommendation.action}",
                    f"**Impact / effort:** {recommendation.impact} / "
                    f"{recommendation.effort}",
                    f"**Confidence:** {recommendation.confidence}",
                    f"**Why and validation:** {recommendation.validation_step}",
                    f"**Evidence:** {citation}",
                ]
            )
            if recommendation.counterevidence:
                lines.append(
                    f"**Counterevidence:** {'; '.join(recommendation.counterevidence)}"
                )
    else:
        lines.append("No ranked priorities were supported by the available evidence.")
    lines.extend(
        [
            "",
            "## Risks and counterevidence",
            _bullet(output.risks_and_counterevidence),
            "",
            "## Coverage and caveats",
            _bullet(output.coverage_and_caveats),
        ]
    )
    rendered = "\n".join(lines).strip() + "\n"
    count = _words(rendered)
    if count < limits.min_words or count > limits.max_words:
        raise ValueError(
            f"rendered report has {count} words; expected "
            f"{limits.min_words}-{limits.max_words}"
        )
    return rendered


def output_from_dict(data: dict[str, Any]) -> Stage2Output:
    """Parse persisted canonical JSON through the Stage 2 contract."""
    return Stage2Output.from_mapping(data)
