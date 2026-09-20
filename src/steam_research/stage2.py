"""Bounded Stage 2 synthesis payload and structured result contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

STAGE2_PROMPT_VERSION = "stage2-prompt-v1"
STAGE2_SCHEMA_VERSION = "stage2-v1"
_GEOGRAPHY_TERMS = re.compile(
    r"\b(country|countries|region|regional|located|geography|nationality|"
    r"france|germany|japan|korea|spain|united states|america|europe)\b",
    re.I,
)


class Stage2ValidationError(ValueError):
    """Raised when a bounded Stage 2 contract is invalid."""


@dataclass(frozen=True)
class Stage2Limits:
    max_evidence: int = 24
    max_metrics: int = 120
    max_priorities: int = 8
    max_vulnerabilities: int = 8
    max_risks: int = 8
    max_findings: int = 3

    def validate(self) -> None:
        if any(value < 0 for value in self.__dict__.values()):
            raise Stage2ValidationError("Stage 2 limits must not be negative")


@dataclass(frozen=True)
class MetricReference:
    metric_id: str
    metric_name: str
    numerator: int
    denominator: int
    value: float | None
    population: str
    dimensions: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.metric_id or not self.metric_name or not self.population:
            raise Stage2ValidationError(
                "metric references require identity and population"
            )
        if self.numerator < 0 or self.denominator <= 0:
            raise Stage2ValidationError(
                "metric references require a positive denominator"
            )
        if self.value is not None and (self.value < 0 or self.value > 1):
            raise Stage2ValidationError(
                "metric value must be null or between zero and one"
            )
        if any(
            key.casefold() in {"country", "region", "geography"}
            for key in self.dimensions
        ):
            raise Stage2ValidationError("metric dimensions must not claim geography")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> MetricReference:
        required = (
            "metric_id",
            "metric_name",
            "numerator",
            "denominator",
            "population",
        )
        if any(key not in data for key in required):
            raise Stage2ValidationError("metric reference is missing a required field")
        return cls(
            metric_id=str(data["metric_id"]),
            metric_name=str(data["metric_name"]),
            numerator=int(data["numerator"]),
            denominator=int(data["denominator"]),
            value=None if data.get("value") is None else float(data["value"]),
            population=str(data["population"]),
            dimensions={
                str(k): str(v) for k, v in dict(data.get("dimensions", {})).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "metric_name": self.metric_name,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "population": self.population,
            "dimensions": dict(self.dimensions),
        }


@dataclass(frozen=True)
class EvidenceReference:
    selection_id: str
    review_id: str
    appid: int
    language: str
    excerpt: str
    direction: str
    source_hash: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.selection_id,
                self.review_id,
                self.language,
                self.excerpt,
                self.source_hash,
            )
        ):
            raise Stage2ValidationError(
                "evidence references require lineage and excerpt"
            )
        if self.appid <= 0 or self.direction not in {"confirming", "counterevidence"}:
            raise Stage2ValidationError(
                "evidence reference has invalid app or direction"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "review_id": self.review_id,
            "appid": self.appid,
            "language": self.language,
            "excerpt": self.excerpt,
            "direction": self.direction,
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True)
class Stage2EvidencePayload:
    project_id: str
    aggregate_run_id: str
    metrics: tuple[MetricReference, ...]
    evidence: tuple[EvidenceReference, ...]
    competitors: tuple[Mapping[str, Any], ...] = ()
    limits: Stage2Limits = Stage2Limits()
    prompt_version: str = STAGE2_PROMPT_VERSION
    schema_version: str = STAGE2_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.limits.validate()
        if not self.project_id or not self.aggregate_run_id:
            raise Stage2ValidationError(
                "payload requires project and aggregate run identity"
            )
        if (
            self.prompt_version != STAGE2_PROMPT_VERSION
            or self.schema_version != STAGE2_SCHEMA_VERSION
        ):
            raise Stage2ValidationError("unsupported Stage 2 contract version")
        if (
            len(self.metrics) > self.limits.max_metrics
            or len(self.evidence) > self.limits.max_evidence
        ):
            raise Stage2ValidationError("payload exceeds configured evidence limits")
        if any(
            "review_text" in item or "raw_json" in item for item in self.competitors
        ):
            raise Stage2ValidationError(
                "Stage 2 payload must not contain raw review corpus fields"
            )
        metric_ids = [item.metric_id for item in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise Stage2ValidationError("metric references must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "aggregate_run_id": self.aggregate_run_id,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "limits": self.limits.__dict__,
            "competitors": [dict(item) for item in self.competitors],
            "metrics": [item.to_dict() for item in self.metrics],
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class Recommendation:
    rank: int
    title: str
    action: str
    impact: str
    effort: str
    evidence_metric_ids: tuple[str, ...]
    evidence_review_ids: tuple[str, ...]
    validation_step: str
    confidence: str
    counterevidence: tuple[str, ...] = ()

    def validate(self, metric_ids: set[str], review_ids: set[str]) -> None:
        if self.rank < 1 or not all(
            (self.title, self.action, self.impact, self.effort, self.validation_step)
        ):
            raise Stage2ValidationError(
                "recommendations require rank, action, impact, effort, and validation"
            )
        if self.impact not in {"high", "medium", "low"} or self.effort not in {
            "high",
            "medium",
            "low",
            "unknown",
        }:
            raise Stage2ValidationError("recommendation impact or effort is invalid")
        if self.confidence not in {"high", "medium", "low"}:
            raise Stage2ValidationError("recommendation confidence is invalid")
        if (
            not set(self.evidence_metric_ids) <= metric_ids
            or not set(self.evidence_review_ids) <= review_ids
        ):
            raise Stage2ValidationError("recommendation references unknown evidence")
        if _GEOGRAPHY_TERMS.search(
            " ".join((self.title, self.action, self.validation_step))
        ):
            raise Stage2ValidationError(
                "recommendations must not make geography claims"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "title": self.title,
            "action": self.action,
            "impact": self.impact,
            "effort": self.effort,
            "evidence_metric_ids": list(self.evidence_metric_ids),
            "evidence_review_ids": list(self.evidence_review_ids),
            "validation_step": self.validation_step,
            "confidence": self.confidence,
            "counterevidence": list(self.counterevidence),
        }


@dataclass(frozen=True)
class Stage2Output:
    status: str
    executive_direction: tuple[str, ...]
    recommendations: tuple[Recommendation, ...]
    risks_and_counterevidence: tuple[str, ...]
    coverage_and_caveats: tuple[str, ...]
    competitor_grid: tuple[Mapping[str, Any], ...] = ()
    schema_version: str = STAGE2_SCHEMA_VERSION

    def validate(self, payload: Stage2EvidencePayload) -> None:
        if (
            self.status not in {"complete", "insufficient_evidence"}
            or self.schema_version != STAGE2_SCHEMA_VERSION
        ):
            raise Stage2ValidationError("invalid Stage 2 output status or version")
        if len(self.executive_direction) > payload.limits.max_findings:
            raise Stage2ValidationError("too many executive findings")
        if len(self.recommendations) > payload.limits.max_priorities:
            raise Stage2ValidationError("too many recommendations")
        if len(self.risks_and_counterevidence) > payload.limits.max_risks:
            raise Stage2ValidationError("too many risks")
        if self.status == "insufficient_evidence" and not self.coverage_and_caveats:
            raise Stage2ValidationError("insufficient evidence requires a caveat")
        metric_ids = {metric.metric_id for metric in payload.metrics}
        review_ids = {evidence.review_id for evidence in payload.evidence}
        for recommendation in self.recommendations:
            recommendation.validate(metric_ids, review_ids)
        if _GEOGRAPHY_TERMS.search(
            " ".join(self.executive_direction + self.risks_and_counterevidence)
        ):
            raise Stage2ValidationError("Stage 2 output must not make geography claims")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "executive_direction": list(self.executive_direction),
            "recommendations": [item.to_dict() for item in self.recommendations],
            "risks_and_counterevidence": list(self.risks_and_counterevidence),
            "coverage_and_caveats": list(self.coverage_and_caveats),
            "competitor_grid": [dict(item) for item in self.competitor_grid],
        }


_DEFAULT_LIMITS = Stage2Limits()


def stage2_json_schema(limits: Stage2Limits | None = None) -> dict[str, Any]:
    """Return the provider-independent schema envelope used by future synthesis."""
    limits = limits or _DEFAULT_LIMITS
    limits.validate()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": STAGE2_SCHEMA_VERSION,
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "status",
            "executive_direction",
            "recommendations",
            "risks_and_counterevidence",
            "coverage_and_caveats",
        ],
        "properties": {
            "schema_version": {"const": STAGE2_SCHEMA_VERSION},
            "status": {"enum": ["complete", "insufficient_evidence"]},
            "executive_direction": {
                "type": "array",
                "maxItems": limits.max_findings,
                "items": {"type": "string"},
            },
            "recommendations": {
                "type": "array",
                "maxItems": limits.max_priorities,
                "items": {"type": "object"},
            },
            "risks_and_counterevidence": {
                "type": "array",
                "maxItems": limits.max_risks,
                "items": {"type": "string"},
            },
            "coverage_and_caveats": {"type": "array", "items": {"type": "string"}},
        },
    }
