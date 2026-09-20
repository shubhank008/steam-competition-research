"""Integration and scale coverage for canonical review storage."""

import json
import time
from dataclasses import replace
from uuid import UUID, uuid4

from steam_research.domain import Run
from steam_research.storage import (
    Database,
    ReviewRecord,
    create_run,
    query_reviews,
    record_api_page,
    update_stream_state,
    upsert_reviews,
)


def _project(database: Database) -> tuple[UUID, UUID]:
    project_id = uuid4()
    run = Run.create(project_id, "review_crawl")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects (id, name, config_path, database_path, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(project_id), "reviews", "config", "db", "now", "now"),
        )
    create_run(database, run)
    return project_id, run.id


def _review(
    project_id: UUID,
    run_id: UUID,
    *,
    index: int = 1,
    voted_up: bool = True,
    raw: str | None = None,
) -> ReviewRecord:
    return ReviewRecord(
        project_id,
        440,
        str(index),
        "english" if index % 2 else "german",
        voted_up,
        2,
        1,
        0.5,
        0,
        True,
        False,
        False,
        False,
        None,
        index * 10,
        100,
        5,
        None,
        1700000000 + index,
        1700000100 + index,
        f"Review {index}",
        "positive" if voted_up else "negative",
        f"{index:064x}",
        raw
        or json.dumps(
            {"recommendationid": str(index), "review": f"Review {index}"},
            separators=(",", ":"),
        ),
        run_id,
    )


def test_round_trip_counts_and_query_filters(database: Database) -> None:
    database.migrate()
    project_id, run_id = _project(database)
    raw = (
        '{"recommendationid":"1","review":"Exact\\ntext",'
        '"author":{"steamid":"private"}}'
    )
    first = _review(project_id, run_id, raw=raw)
    assert upsert_reviews(database, [first]).new == 1
    assert upsert_reviews(database, [first]).unchanged == 1
    changed = replace(
        _review(project_id, run_id, voted_up=False, raw=raw.replace("Exact", "Edited")),
        source_hash="b" * 64,
    )
    counts = upsert_reviews(database, [changed, _review(project_id, run_id, index=2)])
    assert counts.new == 1 and counts.changed == 1 and counts.unchanged == 0
    row = database.connection.execute(
        "SELECT raw_json, voted_up FROM reviews WHERE recommendationid = '1'"
    ).fetchone()
    assert row[0] == raw.replace("Exact", "Edited") and row[1] == 0
    assert (
        len(
            query_reviews(database, project_id=project_id, appid=440, language="german")
        )
        == 1
    )
    assert (
        len(
            query_reviews(
                database, project_id=project_id, voted_up=False, playtime_min=10
            )
        )
        == 1
    )
    assert (
        len(query_reviews(database, project_id=project_id, created_after=1700000002))
        == 1
    )


def test_api_page_and_independent_stream_state(database: Database) -> None:
    database.migrate()
    project_id, _ = _project(database)
    page_id = str(uuid4())
    record_api_page(
        database,
        {
            "id": page_id,
            "project_id": str(project_id),
            "appid": 440,
            "review_type": "positive",
            "request_json": "{}",
            "cursor_in": "*",
            "cursor_out": "next",
            "http_status": 200,
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "review_count": 1,
            "response_hash": "a" * 64,
        },
    )
    update_stream_state(
        database,
        {
            "project_id": str(project_id),
            "appid": 440,
            "review_type": "positive",
            "cursor": "next",
            "status": "partial",
            "last_page_id": page_id,
        },
    )
    update_stream_state(
        database,
        {
            "project_id": str(project_id),
            "appid": 440,
            "review_type": "negative",
            "cursor": "*",
            "status": "pending",
        },
    )
    assert (
        database.connection.execute(
            "SELECT COUNT(*) FROM review_stream_state"
        ).fetchone()[0]
        == 2
    )


def test_100k_generated_batch_baseline(database: Database) -> None:
    database.migrate()
    project_id, run_id = _project(database)
    reviews = [_review(project_id, run_id, index=index) for index in range(100_000)]
    started = time.perf_counter()
    counts = upsert_reviews(database, reviews)
    elapsed = time.perf_counter() - started
    print(f"100000 review inserts: {elapsed:.3f}s ({100000 / elapsed:.0f}/s)")
    assert counts.new == 100_000
    assert (
        database.connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        == 100_000
    )
