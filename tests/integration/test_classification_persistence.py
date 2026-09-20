import json
from pathlib import Path
from uuid import uuid4

from steam_research.classification import (
    BatchLimits,
    ClassificationInput,
    Classifier,
)
from steam_research.config import FilteringConfig
from steam_research.domain import Run
from steam_research.eligibility import decide_review, persist_eligibility
from steam_research.llm import (
    ProviderCapabilities,
    StructuredGenerationResponse,
    StructuredMode,
    Usage,
)
from steam_research.storage import Database, ReviewRecord, create_run, upsert_review
from steam_research.taxonomy import load_taxonomy

TAXONOMY = load_taxonomy(Path("config/taxonomies/universal-core.yaml"))


def output(value: ClassificationInput) -> dict[str, object]:
    return {
        "input_id": value.input_id,
        "review_id": value.recommendationid,
        "is_actionable": True,
        "engagement_posture": "mixed",
        "overall_sentiment": "negative",
        "sentiment_intensity": 3,
        "aspects": [],
        "use_cases": [],
        "self_reported_abandonment": {
            "mentioned": False,
            "statement_type": None,
            "evidence": None,
            "verified": False,
        },
        "summary": "A bounded summary.",
    }


class FakeProvider:
    capabilities = ProviderCapabilities(native_schema=True)

    def __init__(self, valid: bool) -> None:
        self.valid = valid
        self.calls = 0

    def generate_structured(self, request: object) -> StructuredGenerationResponse:
        self.calls += 1
        content = "not-json"
        if self.valid:
            content = json.dumps([output(self.value)])
        return StructuredGenerationResponse(
            content=content,
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            model="fake-model",
            mode=StructuredMode.NATIVE_SCHEMA,
        )

    def set_value(self, value: ClassificationInput) -> None:
        self.value = value


def review_input(project_id: str, run_id: str) -> ClassificationInput:
    return ClassificationInput(
        project_id,
        10,
        "review-1",
        "source-1",
        "eligibility-1",
        "english",
        False,
        12,
        120,
        1_700_000_001,
        1_700_000_002,
        "crash after launch",
    )


def seed_review(
    database: Database, project_id: str, run_id: str
) -> ClassificationInput:
    review = ReviewRecord(
        project_id,
        10,
        "review-1",
        "english",
        False,
        12,
        0,
        None,
        0,
        True,
        False,
        False,
        False,
        None,
        120,
        120,
        0,
        None,
        1_700_000_001,
        1_700_000_002,
        "crash after launch",
        "negative",
        "source-1",
        '{"recommendationid":"review-1"}',
        run_id,
    )
    upsert_review(database, review)
    decision = decide_review(
        project_id=project_id,
        appid=10,
        recommendationid="review-1",
        source_hash="source-1",
        review_text="crash after launch",
        corpus_size=1,
        config=FilteringConfig(),
        decided_at="now",
    )
    persist_eligibility(database, [decision])
    return review_input(project_id, run_id)


def test_classifier_persists_success_lineage(database: Database) -> None:
    database.migrate()
    project_id = uuid4()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects (id, name, config_path, database_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",  # noqa: E501
            (str(project_id), "p", "config", "db", "now", "now"),
        )
    crawl_run = Run.create(project_id, "review_crawl")
    create_run(database, crawl_run)
    value = seed_review(database, str(project_id), str(crawl_run.id))
    provider = FakeProvider(True)
    provider.set_value(value)
    classifier = Classifier(database, provider)
    classification_run = classifier.run(
        project_id=str(project_id),
        inputs=[value],
        taxonomy=TAXONOMY,
        model="fake-model",
        model_policy="policy-1",
        limits=BatchLimits(max_items=1),
    )
    row = database.connection.execute(
        "SELECT status, source_hash, taxonomy_hash, prompt_version, result_json FROM review_classifications WHERE run_id = ?",  # noqa: E501
        (classification_run,),
    ).fetchone()
    assert row[0] == "success"
    assert row[1] == "source-1"
    assert row[2] == TAXONOMY.content_hash
    assert row[3] == "stage1-prompt-v1"
    assert json.loads(row[4])["review_id"] == "review-1"


def test_classifier_quarantines_persistent_invalid_output(database: Database) -> None:
    database.migrate()
    project_id = uuid4()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects (id, name, config_path, database_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",  # noqa: E501
            (str(project_id), "p", "config", "db", "now", "now"),
        )
    crawl_run = Run.create(project_id, "review_crawl")
    create_run(database, crawl_run)
    value = seed_review(database, str(project_id), str(crawl_run.id))
    provider = FakeProvider(False)
    provider.set_value(value)
    classifier = Classifier(database, provider)
    classification_run = classifier.run(
        project_id=str(project_id),
        inputs=[value],
        taxonomy=TAXONOMY,
        model="fake-model",
        model_policy="policy-1",
        limits=BatchLimits(max_items=1, repair_attempts=1),
    )
    row = database.connection.execute(
        "SELECT status, error_code FROM review_classifications WHERE run_id = ?",
        (classification_run,),
    ).fetchone()
    assert row[0] == "quarantined"
    assert row[1] == "JSONDecodeError"
    assert provider.calls == 2
