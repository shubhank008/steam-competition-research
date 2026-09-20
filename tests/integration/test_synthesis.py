import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from steam_research.aggregation import aggregate
from steam_research.llm import (
    ProviderCapabilities,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    Usage,
)
from steam_research.stage2 import (
    EvidenceReference,
    MetricReference,
    Stage2EvidencePayload,
)
from steam_research.storage import Database
from steam_research.synthesis import (
    SynthesisCostCeilings,
    Synthesizer,
    load_synthesis_output,
)


@dataclass
class FakeProvider:
    contents: list[str]
    calls: int = 0
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        self.calls += 1
        return StructuredGenerationResponse(
            self.contents.pop(0), Usage(total_tokens=10), "fake-model"
        )


def make_payload(database: Database) -> tuple[str, Any]:
    project_id = str(uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, "synthesis", "config", "db", "now", "now"),
        )
    result = aggregate(database, project_id=project_id)
    payload = Stage2EvidencePayload(
        project_id=project_id,
        aggregate_run_id=result.run_id,
        metrics=(MetricReference("m", "rate", 1, 2, 0.5, "eligible", {}),),
        evidence=(
            EvidenceReference(
                "s", "review-1", 10, "english", "bounded quote", "confirming", "hash"
            ),
        ),
    )
    return project_id, payload


def valid_output() -> str:
    return json.dumps(
        {
            "schema_version": "stage2-v1",
            "status": "complete",
            "executive_direction": ["Focus on reliable onboarding"],
            "recommendations": [
                {
                    "rank": 1,
                    "title": "Improve onboarding",
                    "action": "Test a shorter first session",
                    "impact": "high",
                    "effort": "medium",
                    "evidence_metric_ids": ["m"],
                    "evidence_review_ids": ["review-1"],
                    "validation_step": "Run a usability test",
                    "confidence": "high",
                    "counterevidence": [],
                }
            ],
            "risks_and_counterevidence": [],
            "coverage_and_caveats": ["Coverage is bounded to selected evidence."],
            "competitor_grid": [],
        }
    )


def test_synthesis_persists_bounded_payload_and_output(database: Database) -> None:
    database.migrate()
    _, payload = make_payload(database)
    provider = FakeProvider([valid_output()])
    run_id = Synthesizer(database, provider).run(
        payload=payload, model="model", model_policy="policy"
    )
    output, stored_payload = load_synthesis_output(database, run_id)
    assert provider.calls == 1
    assert isinstance(output, dict)
    assert output["schema_version"] == "stage2-v1"
    assert stored_payload["evidence"][0]["excerpt"] == "bounded quote"
    assert "review_text" not in json.dumps(stored_payload)


def test_synthesis_repairs_invalid_output_once(database: Database) -> None:
    database.migrate()
    _, payload = make_payload(database)
    provider = FakeProvider(["{}", valid_output()])
    run_id = Synthesizer(database, provider).run(
        payload=payload, model="model", model_policy="policy"
    )
    assert provider.calls == 2
    assert (
        database.connection.execute(
            "SELECT status FROM synthesis_runs WHERE id=?", (run_id,)
        ).fetchone()[0]
        == "completed"
    )


def test_synthesis_ceiling_is_resumable_partial(database: Database) -> None:
    database.migrate()
    _, payload = make_payload(database)
    provider = FakeProvider([valid_output()])
    run_id = Synthesizer(database, provider).run(
        payload=payload,
        model="model",
        model_policy="policy",
        ceilings=SynthesisCostCeilings(max_tokens=1),
    )
    assert provider.calls == 0
    assert (
        database.connection.execute(
            "SELECT status FROM synthesis_runs WHERE id=?", (run_id,)
        ).fetchone()[0]
        == "partial"
    )
