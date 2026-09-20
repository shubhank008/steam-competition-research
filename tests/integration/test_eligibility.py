"""Real SQLite persistence tests for eligibility decisions."""

import json
from dataclasses import replace
from uuid import uuid4

from steam_research.config import FilteringConfig
from steam_research.domain import Run
from steam_research.eligibility import decide_review, persist_eligibility
from steam_research.storage import Database, ReviewRecord, create_run, upsert_review


def test_eligibility_decisions_preserve_reviews_and_obsolete_old_policy(
    database: Database,
) -> None:
    database.migrate()
    project_id = uuid4()
    run = Run.create(project_id, "review_crawl")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects ("
            "id, name, config_path, database_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(project_id), "eligibility", "config", "db", "now", "now"),
        )
    create_run(database, run)
    raw = json.dumps({"review": "crash", "private": "unchanged"})
    upsert_review(
        database,
        ReviewRecord(
            project_id,
            10,
            "r1",
            "english",
            True,
            1,
            0,
            None,
            0,
            True,
            False,
            False,
            False,
            None,
            5,
            5,
            0,
            None,
            1,
            2,
            "crash",
            "positive",
            "a" * 64,
            raw,
            run.id,
        ),
    )
    first = decide_review(
        project_id=str(project_id),
        appid=10,
        recommendationid="r1",
        source_hash="a" * 64,
        review_text="crash",
        corpus_size=1,
        config=FilteringConfig(),
        decided_at="2026-01-01T00:00:00+00:00",
    )
    persist_eligibility(database, [first])
    changed_config = replace(FilteringConfig(), policy_version="eligibility-v2")
    second = decide_review(
        project_id=str(project_id),
        appid=10,
        recommendationid="r1",
        source_hash="a" * 64,
        review_text="crash",
        corpus_size=1,
        config=changed_config,
        decided_at="2026-01-02T00:00:00+00:00",
    )
    persist_eligibility(database, [second])
    rows = database.connection.execute(
        "SELECT policy_version, obsolete_at FROM review_eligibility "
        "WHERE project_id = ? ORDER BY policy_version",
        (str(project_id),),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "eligibility-v1" and rows[0][1] is not None
    assert rows[1][0] == "eligibility-v2" and rows[1][1] is None
    stored = database.connection.execute(
        "SELECT review_text, raw_json FROM reviews WHERE recommendationid = 'r1'"
    ).fetchone()
    assert stored[0] == "crash" and stored[1] == raw
