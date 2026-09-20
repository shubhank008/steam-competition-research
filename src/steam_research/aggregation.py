"""Deterministic, bounded aggregation primitives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from steam_research.storage import Database


@dataclass(frozen=True)
class Metric:
    metric_id: str
    metric_name: str
    dimensions: dict[str, str]
    numerator: int
    denominator: int
    population_definition: str
    coverage: dict[str, Any]
    caveats: tuple[str, ...] = ()

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None


@dataclass(frozen=True)
class AggregateResult:
    run_id: str
    metrics: tuple[Metric, ...]
    source_population: int
    classified_population: int


def _cohort(playtime: int | None) -> str:
    if playtime is None:
        return "unknown"
    if playtime < 60:
        return "early"
    if playtime < 600:
        return "established"
    return "long"


def _metric_id(name: str, dimensions: dict[str, str]) -> str:
    payload = json.dumps([name, dimensions], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _coverage(source_count: int, classified_count: int) -> dict[str, Any]:
    return {
        "source_count": source_count,
        "classified_count": classified_count,
        "unclassified_count": max(0, source_count - classified_count),
        "coverage_rate": classified_count / source_count if source_count else None,
    }


def _current_rows(database: Database, project_id: str) -> list[Any]:
    return database.connection.execute(
        """
        SELECT r.*, c.result_json, c.run_id AS classification_run_id, e.policy_hash
        FROM reviews AS r
        JOIN review_eligibility AS e ON e.project_id = r.project_id
          AND e.appid = r.appid AND e.recommendationid = r.recommendationid
          AND e.obsolete_at IS NULL AND e.decision = 'eligible'
        JOIN review_classifications AS c ON c.project_id = r.project_id
          AND c.appid = r.appid AND c.recommendationid = r.recommendationid
          AND c.status = 'success' AND c.source_hash = r.source_hash
        WHERE r.project_id = ?
        ORDER BY r.appid, r.recommendationid, c.updated_at DESC
        """,
        (project_id,),
    ).fetchall()


def aggregate(
    database: Database, *, project_id: str, config: dict[str, Any] | None = None
) -> AggregateResult:
    """Compute current eligible/classified metrics and persist immutable lineage."""
    config = config or {"early_minutes": 60, "long_minutes": 600}
    source_count = database.connection.execute(
        """
        SELECT COUNT(*) FROM reviews AS r JOIN review_eligibility AS e
          ON e.project_id=r.project_id AND e.appid=r.appid
         AND e.recommendationid=r.recommendationid
         AND e.obsolete_at IS NULL AND e.decision='eligible'
        WHERE r.project_id = ?
        """,
        (project_id,),
    ).fetchone()[0]
    rows = _current_rows(database, project_id)
    rows = list({(row["appid"], row["recommendationid"]): row for row in rows}.values())
    groups: dict[tuple[str, str], list[tuple[Any, dict[str, Any]]]] = {}
    for row in rows:
        groups.setdefault((str(row["appid"]), row["language"]), []).append(
            (row, json.loads(row["result_json"]))
        )

    metrics: list[Metric] = []
    postures = (
        "advocacy",
        "satisfied",
        "qualified_satisfaction",
        "mixed",
        "frustrated_but_engaged",
        "rejection",
        "unclear",
    )
    for (appid, language), values in sorted(groups.items()):
        coverage = _coverage(len(values), len(values))
        for polarity in ("positive", "negative"):
            subset = [
                (row, result)
                for row, result in values
                if bool(row["voted_up"]) == (polarity == "positive")
            ]
            dimensions = {"appid": appid, "language": language, "polarity": polarity}
            metrics.append(
                Metric(
                    _metric_id("review_count", dimensions),
                    "review_count",
                    dimensions,
                    len(subset),
                    len(values),
                    "eligible classified reviews",
                    coverage,
                )
            )
            for posture in postures:
                dims = {**dimensions, "posture": posture}
                metrics.append(
                    Metric(
                        _metric_id("posture_rate", dims),
                        "posture_rate",
                        dims,
                        sum(
                            result["engagement_posture"] == posture
                            for _, result in subset
                        ),
                        len(subset),
                        "classified reviews of polarity",
                        coverage,
                    )
                )

            taxonomy_ids = sorted(
                {
                    aspect["taxonomy_id"]
                    for _, result in subset
                    for aspect in result.get("aspects", [])
                }
            )
            for taxonomy_id in taxonomy_ids:
                for sentiment in ("positive", "negative", "mixed", "neutral"):
                    dims = {
                        **dimensions,
                        "taxonomy_id": taxonomy_id,
                        "sentiment": sentiment,
                    }
                    matching = sum(
                        any(
                            aspect["taxonomy_id"] == taxonomy_id
                            and aspect["sentiment"] == sentiment
                            for aspect in result.get("aspects", [])
                        )
                        for _, result in subset
                    )
                    metrics.append(
                        Metric(
                            _metric_id("aspect_rate", dims),
                            "aspect_rate",
                            dims,
                            matching,
                            len(subset),
                            "classified reviews of polarity",
                            coverage,
                        )
                    )
            for metric_name, numerator, caveat in (
                (
                    "refunded_rate",
                    sum(row["refunded"] for row, _ in subset),
                    "API refund fact is distinct from textual abandonment evidence",
                ),
                (
                    "abandonment_mention_rate",
                    sum(
                        result.get("self_reported_abandonment", {}).get(
                            "mentioned", False
                        )
                        for _, result in subset
                    ),
                    "self-reported abandonment is unverified text evidence",
                ),
            ):
                metrics.append(
                    Metric(
                        _metric_id(metric_name, dimensions),
                        metric_name,
                        dimensions,
                        numerator,
                        len(subset),
                        "classified reviews of polarity",
                        coverage,
                        (caveat,),
                    )
                )
            metrics.append(
                Metric(
                    _metric_id("helpfulness_visibility", dimensions),
                    "helpfulness_visibility",
                    dimensions,
                    sum(row["votes_up"] for row, _ in subset),
                    sum(row["votes_up"] for row, _ in subset),
                    "up-vote visibility among classified reviews of polarity",
                    coverage,
                    ("visibility weighting is separate from unweighted prevalence",),
                )
            )
            for cohort in ("early", "established", "long", "unknown"):
                cohort_rows = [
                    (row, result)
                    for row, result in subset
                    if _cohort(row["playtime_at_review"]) == cohort
                ]
                dims = {**dimensions, "cohort": cohort}
                caveats = (
                    ("low playtime indicates early friction, not verified churn",)
                    if cohort == "early"
                    else ()
                )
                metrics.append(
                    Metric(
                        _metric_id("friction_rate", dims),
                        "friction_rate",
                        dims,
                        sum(
                            result["engagement_posture"]
                            in {"rejection", "frustrated_but_engaged"}
                            for _, result in cohort_rows
                        ),
                        len(cohort_rows),
                        "classified reviews in playtime cohort",
                        coverage,
                        caveats,
                    )
                )

    classified_count = len(rows)
    lineage = {
        "source_hashes": sorted({row["source_hash"] for row in rows}),
        "classification_run_ids": sorted(
            {row["classification_run_id"] for row in rows}
        ),
        "eligibility_policy_hashes": sorted({row["policy_hash"] for row in rows}),
        "config": config,
    }
    run_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True).encode()
    ).hexdigest()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO aggregate_runs VALUES (?, ?, ?, ?, ?, 'completed', ?, ?)",
            (
                run_id,
                project_id,
                config_hash,
                source_count,
                classified_count,
                now,
                json.dumps(lineage, sort_keys=True),
            ),
        )
        for metric in metrics:
            connection.execute(
                "INSERT INTO aggregate_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    metric.metric_id,
                    metric.metric_name,
                    json.dumps(metric.dimensions, sort_keys=True),
                    metric.numerator,
                    metric.denominator,
                    metric.value,
                    metric.population_definition,
                    json.dumps(
                        _coverage(source_count, classified_count), sort_keys=True
                    ),
                    json.dumps(metric.caveats),
                ),
            )
    return AggregateResult(run_id, tuple(metrics), source_count, classified_count)


@dataclass(frozen=True)
class Evidence:
    selection_id: str
    project_id: str
    appid: int
    recommendationid: str
    taxonomy_id: str | None
    evidence: str
    selection_reason: str
    score: float
    direction: str
    language: str
    source_hash: str


def select_evidence(
    database: Database,
    *,
    aggregate_run_id: str,
    limit: int = 12,
    max_per_language: int = 3,
    max_quote_chars: int = 280,
) -> tuple[Evidence, ...]:
    """Select deterministic source-bounded evidence from current classifications."""
    if limit < 0 or max_per_language < 1 or max_quote_chars < 1:
        raise ValueError("evidence limits must be positive, with non-negative limit")
    run = database.connection.execute(
        "SELECT project_id FROM aggregate_runs WHERE id = ?", (aggregate_run_id,)
    ).fetchone()
    if run is None:
        raise ValueError("unknown aggregate run")
    rows = database.connection.execute(
        """
        SELECT r.project_id, r.appid, r.recommendationid, r.language, r.source_hash,
               c.result_json
        FROM reviews r JOIN review_eligibility e ON e.project_id=r.project_id
          AND e.appid=r.appid AND e.recommendationid=r.recommendationid
          AND e.obsolete_at IS NULL AND e.decision='eligible'
        JOIN review_classifications c ON c.project_id=r.project_id AND c.appid=r.appid
          AND c.recommendationid=r.recommendationid AND c.status='success'
          AND c.source_hash=r.source_hash
        WHERE r.project_id=? ORDER BY r.appid, r.recommendationid
        """,
        (run[0],),
    ).fetchall()
    candidates: list[Evidence] = []
    for row in rows:
        result = json.loads(row["result_json"])
        for aspect in result.get("aspects", []):
            quote = aspect.get("evidence", "").strip()
            if not quote or len(quote) > max_quote_chars:
                continue
            direction = (
                "confirming"
                if aspect["sentiment"] == result["overall_sentiment"]
                else "counterevidence"
            )
            seed = (
                f"{row['project_id']}:{row['appid']}:{row['recommendationid']}:"
                f"{aspect['taxonomy_id']}:{quote}"
            )
            score = (
                (
                    int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
                    / 2**64
                )
                + aspect.get("confidence", 0.0)
                + row["appid"] * 0
            )
            candidates.append(
                Evidence(
                    seed[:24],
                    row["project_id"],
                    row["appid"],
                    row["recommendationid"],
                    aspect["taxonomy_id"],
                    quote,
                    "confidence_then_stable_diversity",
                    score,
                    direction,
                    row["language"],
                    row["source_hash"],
                )
            )
    selected: list[Evidence] = []
    counts: dict[str, int] = {}
    seen_quotes: set[str] = set()
    for candidate in sorted(
        candidates,
        key=lambda item: (
            -item.score,
            item.appid,
            item.recommendationid,
            item.taxonomy_id or "",
            item.evidence,
        ),
    ):
        if len(selected) >= limit:
            break
        normalized = " ".join(candidate.evidence.casefold().split())
        if (
            normalized in seen_quotes
            or counts.get(candidate.language, 0) >= max_per_language
        ):
            continue
        selected.append(candidate)
        counts[candidate.language] = counts.get(candidate.language, 0) + 1
        seen_quotes.add(normalized)
    with database.transaction() as connection:
        for item in selected:
            connection.execute(
                "INSERT OR REPLACE INTO quote_selections "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    aggregate_run_id,
                    item.selection_id,
                    item.project_id,
                    item.appid,
                    item.recommendationid,
                    item.taxonomy_id,
                    item.evidence,
                    item.selection_reason,
                    item.score,
                    item.direction,
                    item.language,
                    item.source_hash,
                ),
            )
    return tuple(selected)
