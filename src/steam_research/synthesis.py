"""Bounded Stage 2 synthesis execution and persistence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from steam_research.llm import (
    GenerationMessage,
    GenerationProvider,
    ProviderError,
    StructuredGenerationRequest,
)
from steam_research.stage2 import (
    STAGE2_PROMPT_VERSION,
    STAGE2_SCHEMA_VERSION,
    Stage2EvidencePayload,
    Stage2Output,
    Stage2ValidationError,
    stage2_json_schema,
)
from steam_research.storage import Database


@dataclass(frozen=True)
class SynthesisLimits:
    repair_attempts: int = 1
    max_output_tokens: int = 4096

    def __post_init__(self) -> None:
        if self.repair_attempts < 0 or self.max_output_tokens < 1:
            raise ValueError("synthesis limits are invalid")


@dataclass(frozen=True)
class SynthesisCostCeilings:
    max_requests: int = 0
    max_tokens: int = 0
    max_cost_usd: float = 0.0

    def reached(self, requests: int, tokens: int, cost: float) -> bool:
        return (
            (self.max_requests > 0 and requests >= self.max_requests)
            or (self.max_tokens > 0 and tokens >= self.max_tokens)
            or (self.max_cost_usd > 0 and cost >= self.max_cost_usd)
        )


def build_prompt(payload: Stage2EvidencePayload) -> tuple[str, str]:
    """Build a bounded, versioned prompt from the prepared payload only."""
    system = (
        f"You are a careful competitive strategy analyst. Return only JSON matching "
        f"{STAGE2_SCHEMA_VERSION}. Do not infer geography from language. "
        "Cite only supplied metric_id and review_id values."
    )
    user = json.dumps(
        {
            "prompt_version": STAGE2_PROMPT_VERSION,
            "evidence_payload": payload.to_dict(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return system, user


def _parse_output(content: str, payload: Stage2EvidencePayload) -> Stage2Output:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise Stage2ValidationError("provider output is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise Stage2ValidationError("provider output must be a JSON object")
    output = Stage2Output.from_mapping(parsed)
    output.validate(payload)
    return output


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Synthesizer:
    """Run bounded Stage 2 synthesis and persist immutable lineage."""

    def __init__(self, database: Database, provider: GenerationProvider) -> None:
        self.database = database
        self.provider = provider

    def run(
        self,
        *,
        payload: Stage2EvidencePayload,
        model: str,
        model_policy: str,
        limits: SynthesisLimits | None = None,
        ceilings: SynthesisCostCeilings | None = None,
    ) -> str:
        limits = limits or SynthesisLimits()
        ceilings = ceilings or SynthesisCostCeilings()
        system, user = build_prompt(payload)
        prompt_hash = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()
        run_id = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO synthesis_runs "
                "(id, project_id, aggregate_run_id, prompt_version, prompt_hash, "
                "schema_version, model_policy, payload_json, status, request_ceiling, "
                "token_ceiling, cost_ceiling_usd, requests_used, tokens_used, "
                "cost_used_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', "
                "?, ?, ?, 0, 0, 0, ?)",
                (
                    run_id,
                    payload.project_id,
                    payload.aggregate_run_id,
                    STAGE2_PROMPT_VERSION,
                    prompt_hash,
                    STAGE2_SCHEMA_VERSION,
                    model_policy,
                    json.dumps(payload.to_dict(), sort_keys=True, ensure_ascii=False),
                    ceilings.max_requests,
                    ceilings.max_tokens,
                    ceilings.max_cost_usd,
                    _now(),
                ),
            )

        estimated_tokens = (
            max(1, (len(system) + len(user)) // 4) + limits.max_output_tokens
        )
        if ceilings.reached(0, 0, 0.0) or (
            ceilings.max_tokens > 0 and estimated_tokens > ceilings.max_tokens
        ):
            self._finish(run_id, "partial", "cost ceiling reached before request")
            return run_id

        request = StructuredGenerationRequest(
            model=model,
            messages=(
                GenerationMessage("system", system),
                GenerationMessage("user", user),
            ),
            schema_name=STAGE2_SCHEMA_VERSION,
            schema=stage2_json_schema(payload.limits),
            max_output_tokens=limits.max_output_tokens,
            metadata={"stage": "stage2", "run_id": run_id},
        )
        attempts = 0
        total_tokens = 0
        total_cost = 0.0
        last_error: str | None = None
        while attempts <= limits.repair_attempts:
            if ceilings.reached(attempts, total_tokens, total_cost):
                self._finish(run_id, "partial", "cost ceiling reached")
                return run_id
            attempts += 1
            try:
                response = self.provider.generate_structured(request)
                usage = response.usage
                total_tokens += usage.total_tokens or 0
                total_cost += usage.estimated_cost_usd or 0.0
                output = _parse_output(response.content, payload)
            except (ProviderError, Stage2ValidationError) as error:
                last_error = str(error)
                continue
            self._persist_success(
                run_id, output, response.model, attempts, total_tokens, total_cost
            )
            return run_id
        self._finish(run_id, "failed", last_error or "synthesis failed")
        return run_id

    def _persist_success(
        self,
        run_id: str,
        output: Stage2Output,
        model: str,
        attempts: int,
        tokens: int,
        cost: float,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE synthesis_runs SET status='completed', attempts=?, model=?, "
                "output_json=?, requests_used=?, tokens_used=?, cost_used_usd=?, "
                "completed_at=? WHERE id=?",
                (
                    attempts,
                    model,
                    json.dumps(output.to_dict(), sort_keys=True),
                    attempts,
                    tokens,
                    cost,
                    _now(),
                    run_id,
                ),
            )

    def _finish(self, run_id: str, status: str, error: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE synthesis_runs SET status=?, error_summary=?, "
                "completed_at=? WHERE id=?",
                (status, error[:500], _now(), run_id),
            )


def load_synthesis_output(
    database: Database, run_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = database.connection.execute(
        "SELECT output_json, payload_json FROM synthesis_runs "
        "WHERE id=? AND status='completed'",
        (run_id,),
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError("completed synthesis output not found")
    return json.loads(row[0]), json.loads(row[1])
