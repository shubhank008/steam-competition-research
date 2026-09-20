"""Integration tests for resumable dual-stream crawling and refresh."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from steam_research.domain import AppId, ExternalError, ExternalErrorCode, Run
from steam_research.steam import (
    CrawlLimits,
    ReviewPage,
    ReviewPageRequest,
    crawl_reviews,
)
from steam_research.steam.contracts import Review, ReviewType, source_hash
from steam_research.storage import Database, create_run


@dataclass
class FixtureSource:
    pages: dict[tuple[ReviewType, str], ReviewPage]
    calls: list[ReviewPageRequest]
    crash_after: int | None = None

    def fetch_page(self, request: ReviewPageRequest) -> ReviewPage:
        self.calls.append(request)
        if self.crash_after is not None and len(self.calls) > self.crash_after:
            raise RuntimeError("injected crash")
        return self.pages[(request.review_type, request.cursor)]


def review(index: str, *, voted_up: bool, updated: int) -> Review:
    raw = {
        "recommendationid": index,
        "language": "english",
        "review": f"Review {index}",
        "voted_up": voted_up,
        "timestamp_created": updated - 100,
        "timestamp_updated": updated,
    }
    import json

    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return Review(
        index,
        "english",
        str(raw["review"]),
        voted_up,
        1,
        0,
        updated - 100,
        updated,
        True,
        False,
        False,
        False,
        encoded,
        source_hash(encoded),
    )


def setup(database: Database) -> tuple[UUID, UUID]:
    database.migrate()
    project_id = uuid4()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
            (str(project_id), "crawler", "config", "db", "now", "now"),
        )
    run = Run.create(project_id, "review_crawl")
    create_run(database, run)
    return project_id, run.id


def pages() -> dict[tuple[ReviewType, str], ReviewPage]:
    return {
        ("positive", "*"): ReviewPage(
            AppId(440),
            "positive",
            "p1",
            (review("p1", voted_up=True, updated=200),),
            {},
        ),
        ("positive", "p1"): ReviewPage(AppId(440), "positive", "p1", (), {}),
        ("negative", "*"): ReviewPage(
            AppId(440),
            "negative",
            "n1",
            (review("n1", voted_up=False, updated=190),),
            {},
        ),
        ("negative", "n1"): ReviewPage(AppId(440), "negative", "n1", (), {}),
    }


def test_dual_streams_checkpoint_and_resume_after_crash(database: Database) -> None:
    project_id, run_id = setup(database)
    source = FixtureSource(pages(), [], crash_after=3)
    with pytest.raises(RuntimeError, match="injected"):
        crawl_reviews(
            database,
            source,
            project_id=project_id,
            run_id=run_id,
            appid=AppId(440),
            sleep=lambda _: None,
        )
    state = database.connection.execute(
        "SELECT review_type, cursor FROM review_stream_state ORDER BY review_type"
    ).fetchall()
    assert [(row[0], row[1]) for row in state] == [
        ("negative", "n1"),
        ("positive", "p1"),
    ]
    source.crash_after = None
    result = crawl_reviews(
        database,
        source,
        project_id=project_id,
        run_id=run_id,
        appid=AppId(440),
        sleep=lambda _: None,
    )
    assert {item.stop_reason for item in result.streams} == {"empty_page"}
    assert (
        database.connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2
    )


def test_incremental_refresh_updates_edits_and_polarity(database: Database) -> None:
    project_id, run_id = setup(database)
    source = FixtureSource(pages(), [])
    crawl_reviews(
        database,
        source,
        project_id=project_id,
        run_id=run_id,
        appid=AppId(440),
        sleep=lambda _: None,
    )
    changed = review("p1", voted_up=False, updated=300)
    source.pages[("positive", "*")] = ReviewPage(
        AppId(440), "positive", "p1", (changed,), {}
    )
    source.pages[("positive", "p1")] = ReviewPage(AppId(440), "positive", "p1", (), {})
    result = crawl_reviews(
        database,
        source,
        project_id=project_id,
        run_id=run_id,
        appid=AppId(440),
        incremental=True,
        sleep=lambda _: None,
    )
    assert any(item.stop_reason == "empty_page" for item in result.streams)
    row = database.connection.execute(
        "SELECT voted_up, source_review_type, timestamp_updated "
        "FROM reviews WHERE recommendationid = 'p1'"
    ).fetchone()
    assert tuple(row) == (0, "positive", 300)
    assert (
        database.connection.execute(
            "SELECT COUNT(*) FROM reviews WHERE recommendationid = 'p1'"
        ).fetchone()[0]
        == 1
    )


def test_limits_are_deterministic_and_cancel_is_resumable(database: Database) -> None:
    project_id, run_id = setup(database)
    source = FixtureSource(pages(), [])
    result = crawl_reviews(
        database,
        source,
        project_id=project_id,
        run_id=run_id,
        appid=AppId(440),
        limits=CrawlLimits(max_reviews_per_app=1),
        sleep=lambda _: None,
    )
    assert [item.stop_reason for item in result.streams] == [
        "configured_limit",
        "configured_limit",
    ]
    assert [request.review_type for request in source.calls] == ["positive", "negative"]


def test_retry_exhaustion_is_explicit(database: Database) -> None:
    project_id, run_id = setup(database)

    class FailingSource:
        def fetch_page(self, request: ReviewPageRequest) -> ReviewPage:
            raise ExternalError(ExternalErrorCode.SERVER, "busy")

    result = crawl_reviews(
        database,
        FailingSource(),
        project_id=project_id,
        run_id=run_id,
        appid=AppId(440),
        limits=CrawlLimits(max_retries=1),
        sleep=lambda _: None,
    )
    assert all(item.stop_reason == "retry_exhausted:server" for item in result.streams)


def test_cancellation_records_partial_streams(database: Database) -> None:
    project_id, run_id = setup(database)
    source = FixtureSource(pages(), [])
    calls = 0

    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    result = crawl_reviews(
        database,
        source,
        project_id=project_id,
        run_id=run_id,
        appid=AppId(440),
        should_cancel=cancel,
        sleep=lambda _: None,
    )
    assert result.cancelled
    assert all(item.stop_reason == "cancelled" for item in result.streams)
    assert (
        database.connection.execute(
            "SELECT COUNT(*) FROM review_stream_state WHERE status = 'partial'"
        ).fetchone()[0]
        == 2
    )


def test_fully_known_overlap_preserves_high_water(database: Database) -> None:
    project_id, run_id = setup(database)
    initial_pages = pages()
    initial_pages[("positive", "*")] = ReviewPage(
        AppId(440),
        "positive",
        "p1",
        (
            review("p1", voted_up=True, updated=200),
            review("p2", voted_up=True, updated=300),
        ),
        {},
    )
    source = FixtureSource(initial_pages, [])
    crawl_reviews(
        database,
        source,
        project_id=project_id,
        run_id=run_id,
        appid=AppId(440),
        sleep=lambda _: None,
    )
    before = database.connection.execute(
        "SELECT high_water_timestamp FROM review_stream_state "
        "WHERE review_type = 'positive'"
    ).fetchone()[0]
    source.pages[("positive", "*")] = ReviewPage(
        AppId(440), "positive", "old", (review("p1", voted_up=True, updated=200),), {}
    )
    source.pages[("positive", "old")] = ReviewPage(
        AppId(440), "positive", "old", (), {}
    )
    result = crawl_reviews(
        database,
        source,
        project_id=project_id,
        run_id=run_id,
        appid=AppId(440),
        incremental=True,
        limits=CrawlLimits(overlap_seconds=50),
        sleep=lambda _: None,
    )
    positive = next(item for item in result.streams if item.review_type == "positive")
    assert positive.stop_reason == "fully_known_overlap"
    after = database.connection.execute(
        "SELECT high_water_timestamp FROM review_stream_state "
        "WHERE review_type = 'positive'"
    ).fetchone()[0]
    assert after == before
