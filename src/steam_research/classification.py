"""Deterministic Stage 1 selection, batching, recovery, and persistence."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from steam_research.eligibility import policy_hash
from steam_research.llm import (
    GenerationMessage,
    GenerationProvider,
    ProviderError,
    StructuredGenerationRequest,
)
from steam_research.stage1 import (
    STAGE1_PROMPT_VERSION,
    STAGE1_SCHEMA_VERSION,
    build_prompt,
    schema,
    validate_results,
)
from steam_research.storage import Database

Scope = Literal["all", "progressive", "stratified", "unclassified-only"]


@dataclass(frozen=True)
class ClassificationInput:
    project_id: str
    appid: int
    recommendationid: str
    source_hash: str
    eligibility_policy_hash: str
    language: str
    voted_up: bool
    votes_up: int
    playtime_at_review: int | None
    timestamp_created: int
    timestamp_updated: int
    review_text: str
    inclusion_reason: str = "eligible"
    sampling_weight: float = 1.0

    @property
    def input_id(self) -> str:
        return f"{self.appid}:{self.recommendationid}"

    def projection(self) -> dict[str, Any]:
        return {
            "input_id": self.input_id,
            "review_id": self.recommendationid,
            "language": self.language,
            "voted_up": self.voted_up,
            "votes_up": self.votes_up,
            "playtime_at_review": self.playtime_at_review,
            "review_text": self.review_text,
        }


@dataclass(frozen=True)
class Batch:
    number: int
    inputs: tuple[ClassificationInput, ...]
    estimated_tokens: int


@dataclass(frozen=True)
class BatchLimits:
    max_items: int = 20
    max_tokens: int = 6000
    repair_attempts: int = 1
    min_split_items: int = 1
    max_output_tokens: int = 4096

    def __post_init__(self) -> None:
        if self.max_items < 1 or self.max_tokens < 1 or self.repair_attempts < 0:
            raise ValueError("batch limits must be positive, with non-negative repairs")
        if not 1 <= self.min_split_items <= self.max_items:
            raise ValueError("min_split_items must be within max_items")


@dataclass(frozen=True)
class CostCeilings:
    max_requests: int = 0
    max_tokens: int = 0
    max_cost_usd: float = 0.0

    def reached(self, requests: int, tokens: int, cost: float) -> bool:
        return (
            (self.max_requests > 0 and requests >= self.max_requests)
            or (self.max_tokens > 0 and tokens >= self.max_tokens)
            or (self.max_cost_usd > 0 and cost >= self.max_cost_usd)
        )


def estimate_tokens(value: str | dict[str, Any]) -> int:
    """Estimate tokens without provider dependencies, conservatively."""
    encoded = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(1, math.ceil(len(encoded) / 4))


def plan_batches(
    inputs: Sequence[ClassificationInput], limits: BatchLimits
) -> tuple[Batch, ...]:
    batches: list[Batch] = []
    current: list[ClassificationInput] = []
    tokens = 0
    for item in inputs:
        item_tokens = estimate_tokens(item.projection())
        if item_tokens > limits.max_tokens:
            raise ValueError(f"input {item.input_id} exceeds token limit")
        if current and (
            len(current) >= limits.max_items or tokens + item_tokens > limits.max_tokens
        ):
            batches.append(Batch(len(batches), tuple(current), tokens))
            current, tokens = [], 0
        current.append(item)
        tokens += item_tokens
    if current:
        batches.append(Batch(len(batches), tuple(current), tokens))
    return tuple(batches)


def _stable_score(item: ClassificationInput, seed: int) -> float:
    digest = hashlib.sha256(
        f"{seed}:{item.project_id}:{item.appid}:{item.recommendationid}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _playtime_bucket(value: int | None) -> str:
    if value is None or value < 60:
        return "early"
    if value < 600:
        return "established"
    return "long"


def _stratum(item: ClassificationInput) -> tuple[str, str, str, str, str]:
    recency = "recent" if item.timestamp_created >= 1_700_000_000 else "older"
    helpfulness = "helpful" if item.votes_up >= 10 else "low_helpfulness"
    return (
        str(item.appid),
        item.language,
        "positive" if item.voted_up else "negative",
        _playtime_bucket(item.playtime_at_review),
        f"{recency}:{helpfulness}",
    )


def select_inputs(
    inputs: Iterable[ClassificationInput],
    *,
    scope: Scope,
    seed: int = 0,
    full_corpus_threshold: int = 10_000,
    ceiling: int = 0,
) -> tuple[ClassificationInput, ...]:
    """Select eligible inputs; selection is stable for identical inputs and seed."""
    population = sorted(inputs, key=lambda item: (item.appid, item.recommendationid))
    if scope not in {"all", "progressive", "stratified", "unclassified-only"}:
        raise ValueError(f"unsupported classification scope: {scope}")
    if scope == "unclassified-only":
        population = [
            item for item in population if item.inclusion_reason != "classified"
        ]
    if scope == "all" or (
        scope == "progressive" and len(population) <= full_corpus_threshold
    ):
        selected = population
    else:
        groups: dict[tuple[str, ...], list[ClassificationInput]] = defaultdict(list)
        for item in population:
            groups[_stratum(item)].append(item)
        selected = []
        ordered_groups = sorted(groups)
        target = ceiling or max(len(ordered_groups), math.ceil(len(population) * 0.1))
        for key in ordered_groups:
            group = sorted(groups[key], key=lambda item: _stable_score(item, seed))
            if group:
                selected.append(group[0])
        remaining = [item for item in population if item not in selected]
        selected.extend(
            sorted(remaining, key=lambda item: _stable_score(item, seed))[
                : max(0, target - len(selected))
            ]
        )
    if ceiling:
        selected = sorted(selected, key=lambda item: _stable_score(item, seed))[
            :ceiling
        ]
    selected.sort(key=lambda item: (item.appid, item.recommendationid))
    weights = len(population) / len(selected) if selected else 1.0
    return tuple(
        ClassificationInput(**{**item.__dict__, "sampling_weight": weights})
        for item in selected
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash_prompt(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()


class Classifier:
    """Execute bounded Stage 1 work against a provider and SQLite."""

    def __init__(self, database: Database, provider: GenerationProvider) -> None:
        self.database = database
        self.provider = provider

    def run(
        self,
        *,
        project_id: str,
        inputs: Sequence[ClassificationInput],
        taxonomy: Any,
        model: str,
        model_policy: str,
        scope: Scope = "all",
        seed: int = 0,
        limits: BatchLimits | None = None,
        ceilings: CostCeilings | None = None,
    ) -> str:
        limits = limits or BatchLimits()
        ceilings = ceilings or CostCeilings()
        selected = select_inputs(inputs, scope=scope, seed=seed)
        batches = plan_batches(selected, limits)
        run_id = str(uuid4())
        prompt_hash = hashlib.sha256(STAGE1_PROMPT_VERSION.encode()).hexdigest()
        policy = hashlib.sha256(model_policy.encode()).hexdigest()
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO classification_runs (id, project_id, scope, seed, source_population, selected_count, prompt_version, prompt_hash, taxonomy_version, taxonomy_hash, model_policy, schema_version, policy_hash, status, request_ceiling, token_ceiling, cost_ceiling_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",  # noqa: E501
                (
                    run_id,
                    project_id,
                    scope,
                    seed,
                    len(inputs),
                    len(selected),
                    STAGE1_PROMPT_VERSION,
                    prompt_hash,
                    taxonomy.version,
                    taxonomy.content_hash,
                    model_policy,
                    STAGE1_SCHEMA_VERSION,
                    policy,
                    ceilings.max_requests,
                    ceilings.max_tokens,
                    ceilings.max_cost_usd,
                    now,
                ),
            )
        used_requests = used_tokens = 0
        used_cost = 0.0
        for batch in batches:
            if ceilings.reached(used_requests, used_tokens, used_cost):
                break
            estimated_request_tokens = batch.estimated_tokens + limits.max_output_tokens
            if (
                ceilings.max_tokens > 0
                and used_tokens + estimated_request_tokens > ceilings.max_tokens
            ):
                break
            if ceilings.max_requests > 0 and used_requests >= ceilings.max_requests:
                break
            self._execute_batch(
                run_id,
                batch,
                taxonomy,
                model,
                model_policy,
                limits,
                ceilings,
                [used_requests, used_tokens, used_cost],
            )
            row = self.database.connection.execute(
                "SELECT requests_used, tokens_used, cost_used_usd FROM classification_runs WHERE id = ?",  # noqa: E501
                (run_id,),
            ).fetchone()
            used_requests, used_tokens, used_cost = (
                int(row[0]),
                int(row[1]),
                float(row[2]),
            )
        processed = self.database.connection.execute(
            "SELECT COUNT(*) FROM review_classifications WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        status = "partial" if processed < len(selected) else "completed"
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE classification_runs SET status = ?, completed_at = ? WHERE id = ?",  # noqa: E501
                (status, _now(), run_id),
            )
        return run_id

    def _execute_batch(
        self,
        run_id: str,
        batch: Batch,
        taxonomy: Any,
        model: str,
        model_policy: str,
        limits: BatchLimits,
        ceilings: CostCeilings,
        usage: list[int | float],
        parent_batch_id: str | None = None,
    ) -> None:
        batch_id = str(uuid4())
        with self.database.transaction() as connection:
            next_number = connection.execute(
                "SELECT COALESCE(MAX(batch_number), -1) + 1 "
                "FROM classification_batches WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO classification_batches (id, run_id, batch_number, status, input_count, estimated_tokens, parent_batch_id) VALUES (?, ?, ?, 'running', ?, ?, ?)",  # noqa: E501
                (
                    batch_id,
                    run_id,
                    next_number,
                    len(batch.inputs),
                    batch.estimated_tokens,
                    parent_batch_id,
                ),
            )
            for item in batch.inputs:
                if parent_batch_id is not None:
                    connection.execute(
                        "UPDATE review_classifications SET batch_id = ?, status = 'pending', "  # noqa: E501
                        "updated_at = ? WHERE run_id = ? AND project_id = ? AND appid = ? "  # noqa: E501
                        "AND recommendationid = ?",
                        (
                            batch_id,
                            _now(),
                            run_id,
                            item.project_id,
                            item.appid,
                            item.recommendationid,
                        ),
                    )
                else:
                    connection.execute(
                        "INSERT INTO review_classifications (run_id, batch_id, project_id, appid, recommendationid, source_hash, eligibility_policy_hash, prompt_version, prompt_hash, taxonomy_version, taxonomy_hash, model_policy, schema_version, status, inclusion_reason, sampling_weight, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",  # noqa: E501
                        (
                            run_id,
                            batch_id,
                            item.project_id,
                            item.appid,
                            item.recommendationid,
                            item.source_hash,
                            item.eligibility_policy_hash,
                            STAGE1_PROMPT_VERSION,
                            hashlib.sha256(STAGE1_PROMPT_VERSION.encode()).hexdigest(),
                            taxonomy.version,
                            taxonomy.content_hash,
                            model_policy,
                            STAGE1_SCHEMA_VERSION,
                            item.inclusion_reason,
                            item.sampling_weight,
                            _now(),
                            _now(),
                        ),
                    )
        reviews = [item.projection() for item in batch.inputs]
        system, user = build_prompt(taxonomy=taxonomy, reviews=reviews)
        request = StructuredGenerationRequest(
            model=model,
            messages=(
                GenerationMessage("system", system),
                GenerationMessage("user", user),
            ),
            schema_name=STAGE1_SCHEMA_VERSION,
            schema=schema(),
            max_output_tokens=limits.max_output_tokens,
            metadata={"run_id": run_id, "batch_id": batch_id},
        )
        try:
            response = self.provider.generate_structured(request)
            usage[0] += 1
            usage[1] += response.usage.total_tokens or response.usage.input_tokens or 0
            usage[2] += response.usage.estimated_cost_usd or 0.0
            raw = json.loads(response.content)
            validate_results(
                raw,
                input_ids={item.input_id for item in batch.inputs},
                review_text_by_input={
                    item.input_id: item.review_text for item in batch.inputs
                },
                taxonomy_ids=taxonomy.ids,
            )
            self._persist_success(run_id, batch_id, batch, raw, response, usage)
        except (ProviderError, ValueError, json.JSONDecodeError) as error:
            if limits.repair_attempts and not isinstance(error, ProviderError):
                try:
                    repair = self.provider.generate_structured(request)
                    usage[0] += 1
                    usage[1] += (
                        repair.usage.total_tokens or repair.usage.input_tokens or 0
                    )
                    usage[2] += repair.usage.estimated_cost_usd or 0.0
                    raw = json.loads(repair.content)
                    validate_results(
                        raw,
                        input_ids={item.input_id for item in batch.inputs},
                        review_text_by_input={
                            item.input_id: item.review_text for item in batch.inputs
                        },
                        taxonomy_ids=taxonomy.ids,
                    )
                    self._persist_success(run_id, batch_id, batch, raw, repair, usage)
                    return
                except (
                    ProviderError,
                    ValueError,
                    json.JSONDecodeError,
                ) as repair_error:
                    error = repair_error
            if len(batch.inputs) > limits.min_split_items:
                midpoint = len(batch.inputs) // 2
                self._mark_batch(batch_id, "failed", str(error))
                for part in (batch.inputs[:midpoint], batch.inputs[midpoint:]):
                    child = Batch(
                        batch.number,
                        tuple(part),
                        sum(estimate_tokens(item.projection()) for item in part),
                    )
                    self._execute_batch(
                        run_id,
                        child,
                        taxonomy,
                        model,
                        model_policy,
                        limits,
                        ceilings,
                        usage,
                        batch_id,
                    )
            else:
                self._persist_failure(
                    run_id,
                    batch_id,
                    batch,
                    "quarantined",
                    type(error).__name__,
                    str(error),
                )

    def _mark_batch(self, batch_id: str, status: str, error: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE classification_batches SET status = ?, attempts = attempts + 1, error_code = ? WHERE id = ?",  # noqa: E501
                (status, error[:120], batch_id),
            )

    def _persist_success(
        self,
        run_id: str,
        batch_id: str,
        batch: Batch,
        raw: list[dict[str, Any]],
        response: Any,
        usage: list[int | float],
    ) -> None:
        by_id = {item["input_id"]: item for item in raw}
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE classification_batches SET status = 'completed', attempts = attempts + 1 WHERE id = ?",  # noqa: E501
                (batch_id,),
            )
            for item in batch.inputs:
                connection.execute(
                    "UPDATE review_classifications SET status = 'success', result_json = ?, input_tokens = ?, output_tokens = ?, estimated_cost_usd = ?, updated_at = ? WHERE run_id = ? AND batch_id = ? AND project_id = ? AND appid = ? AND recommendationid = ?",  # noqa: E501
                    (
                        json.dumps(
                            by_id[item.input_id], sort_keys=True, separators=(",", ":")
                        ),
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                        response.usage.estimated_cost_usd,
                        _now(),
                        run_id,
                        batch_id,
                        item.project_id,
                        item.appid,
                        item.recommendationid,
                    ),
                )
            connection.execute(
                "UPDATE classification_runs SET requests_used = ?, tokens_used = ?, cost_used_usd = ? WHERE id = ?",  # noqa: E501
                (*usage, run_id),
            )

    def _persist_failure(
        self,
        run_id: str,
        batch_id: str,
        batch: Batch,
        status: str,
        code: str,
        detail: str,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE classification_batches SET status = ?, attempts = attempts + 1, error_code = ? WHERE id = ?",  # noqa: E501
                (status, code, batch_id),
            )
            connection.execute(
                "UPDATE review_classifications SET status = ?, error_code = ?, error_detail = ?, updated_at = ? WHERE run_id = ? AND batch_id = ?",  # noqa: E501
                (status, code, detail[:500], _now(), run_id, batch_id),
            )


def current_classifier_inputs(
    database: Database,
    *,
    project_id: str,
    filtering: Any,
    prompt_version: str = STAGE1_PROMPT_VERSION,
    taxonomy_hash: str | None = None,
    model_policy: str | None = None,
    scope: Scope = "unclassified-only",
) -> list[ClassificationInput]:
    """Read eligible reviews and mark only fully compatible successes current."""
    rows = database.connection.execute(
        "SELECT r.*, e.policy_hash FROM reviews r JOIN review_eligibility e ON e.project_id = r.project_id AND e.appid = r.appid AND e.recommendationid = r.recommendationid AND e.obsolete_at IS NULL AND e.decision = 'eligible' WHERE r.project_id = ? ORDER BY r.appid, r.recommendationid",  # noqa: E501
        (project_id,),
    ).fetchall()
    result: list[ClassificationInput] = []
    current_policy = policy_hash(filtering)
    for row in rows:
        compatible = [
            "c.project_id = ?",
            "c.appid = ?",
            "c.recommendationid = ?",
            "c.status = 'success'",
            "c.source_hash = ?",
            "c.eligibility_policy_hash = ?",
            "c.prompt_version = ?",
            "c.schema_version = ?",
        ]
        parameters: list[Any] = [
            project_id,
            row["appid"],
            row["recommendationid"],
            row["source_hash"],
            current_policy,
            prompt_version,
            STAGE1_SCHEMA_VERSION,
        ]
        if taxonomy_hash is not None:
            compatible.append("c.taxonomy_hash = ?")
            parameters.append(taxonomy_hash)
        if model_policy is not None:
            compatible.append("c.model_policy = ?")
            parameters.append(model_policy)
        classified = database.connection.execute(
            "SELECT 1 FROM review_classifications c WHERE "
            + " AND ".join(compatible)
            + " ORDER BY c.updated_at DESC LIMIT 1",
            parameters,
        ).fetchone()
        if scope == "unclassified-only" and classified is not None:
            continue
        result.append(
            ClassificationInput(
                project_id,
                row["appid"],
                row["recommendationid"],
                row["source_hash"],
                current_policy,
                row["language"],
                bool(row["voted_up"]),
                row["votes_up"],
                row["playtime_at_review"],
                row["timestamp_created"],
                row["timestamp_updated"],
                row["review_text"],
                "classified" if classified else "eligible",
            )
        )
    return result
