"""Resumable dual-stream review crawling and incremental refresh."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4

from steam_research.domain import AppId, ExternalError
from steam_research.steam.contracts import (
    PurchaseType,
    Review,
    ReviewFilter,
    ReviewPage,
    ReviewPageRequest,
    ReviewType,
    source_hash,
)
from steam_research.storage import (
    Database,
    ReviewRecord,
    persist_page_checkpoint,
    update_stream_state,
)


class ReviewPageSource(Protocol):
    def fetch_page(self, request: ReviewPageRequest) -> ReviewPage: ...


@dataclass(frozen=True)
class CrawlLimits:
    max_reviews_per_app: int = 0
    max_positive_reviews: int = 0
    max_negative_reviews: int = 0
    max_retries: int = 3
    backoff_seconds: float = 1.0
    overlap_seconds: int = 86400


@dataclass(frozen=True)
class StreamResult:
    review_type: ReviewType
    status: str
    stop_reason: str
    pages: int
    reviews: int
    cursor: str
    high_water_timestamp: int | None


_DEFAULT_LIMITS = CrawlLimits()
_STREAM_TYPES: tuple[ReviewType, ReviewType] = ("positive", "negative")


@dataclass(frozen=True)
class CrawlResult:
    streams: tuple[StreamResult, ...]
    cancelled: bool = False


class CrawlCancelled(Exception):
    """Raised by a caller-controlled cancellation callback."""


@dataclass
class _StreamState:
    review_type: ReviewType
    cursor: str = "*"
    pages: int = 0
    reviews: int = 0
    high_water_timestamp: int | None = None
    previous_high_water: int | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _review_record(
    project_id: UUID,
    run_id: UUID,
    appid: AppId,
    review_type: ReviewType,
    review: Review,
) -> ReviewRecord:
    raw = review.raw_json or json.dumps({"recommendationid": review.recommendationid})
    return ReviewRecord(
        project_id,
        appid.value,
        review.recommendationid,
        review.language,
        review.voted_up,
        review.votes_up,
        review.votes_funny,
        None,
        0,
        review.steam_purchase,
        review.received_for_free,
        review.refunded,
        review.written_during_early_access,
        None,
        None,
        None,
        None,
        None,
        review.timestamp_created,
        review.timestamp_updated,
        review.review,
        review_type,
        review.source_hash or source_hash(raw),
        raw,
        run_id,
    )


def _load_state(
    database: Database, project_id: UUID, appid: AppId, review_type: ReviewType
) -> _StreamState:
    row = database.connection.execute(
        "SELECT cursor, high_water_timestamp FROM review_stream_state "
        "WHERE project_id = ? AND appid = ? AND review_type = ?",
        (str(project_id), appid.value, review_type),
    ).fetchone()
    if row is None:
        return _StreamState(review_type)
    return _StreamState(review_type, row[0] or "*", previous_high_water=row[1])


def _retry_page(
    source: ReviewPageSource,
    request: ReviewPageRequest,
    limits: CrawlLimits,
    sleep: Callable[[float], None],
) -> ReviewPage:
    attempt = 0
    while True:
        try:
            return source.fetch_page(request)
        except ExternalError as exc:
            if not exc.retryable or attempt >= limits.max_retries:
                raise
            delay = exc.retry_after_seconds or limits.backoff_seconds * (2**attempt)
            sleep(delay)
            attempt += 1


def _fully_known_overlap(
    database: Database,
    project_id: UUID,
    appid: AppId,
    page: ReviewPage,
    previous_high_water: int,
    overlap_seconds: int,
) -> bool:
    cutoff = previous_high_water - overlap_seconds
    if not page.reviews or any(
        review.timestamp_updated >= cutoff for review in page.reviews
    ):
        return False
    for review in page.reviews:
        row = database.connection.execute(
            "SELECT timestamp_updated, source_hash FROM reviews WHERE project_id = ? "
            "AND appid = ? AND recommendationid = ?",
            (str(project_id), appid.value, review.recommendationid),
        ).fetchone()
        if (
            row is None
            or row[0] != review.timestamp_updated
            or row[1] != review.source_hash
        ):
            return False
    return True


def crawl_reviews(
    database: Database,
    source: ReviewPageSource,
    *,
    project_id: UUID,
    run_id: UUID,
    appid: AppId,
    language: str = "all",
    purchase_type: str = "all",
    review_filter: str = "updated",
    limits: CrawlLimits = _DEFAULT_LIMITS,
    incremental: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    page_hook: Callable[[ReviewPage], None] | None = None,
) -> CrawlResult:
    """Schedule both streams and checkpoint each page durably."""
    states = [_load_state(database, project_id, appid, kind) for kind in _STREAM_TYPES]
    if incremental:
        for state in states:
            state.cursor = "*"
    active = list(states)
    results: list[StreamResult] = []
    cancelled = False
    try:
        while active:
            next_active: list[_StreamState] = []
            for state in active:
                if should_cancel is not None and should_cancel():
                    raise CrawlCancelled
                limit = (
                    limits.max_positive_reviews
                    if state.review_type == "positive"
                    else limits.max_negative_reviews
                )
                if limit == 0 and limits.max_reviews_per_app:
                    limit = limits.max_reviews_per_app
                if limit and state.reviews >= limit:
                    reason = "configured_limit"
                    update_stream_state(
                        database,
                        {
                            "project_id": str(project_id),
                            "appid": appid.value,
                            "review_type": state.review_type,
                            "cursor": state.cursor,
                            "status": "complete",
                            "stop_reason": reason,
                        },
                    )
                    results.append(
                        StreamResult(
                            state.review_type,
                            "complete",
                            reason,
                            state.pages,
                            state.reviews,
                            state.cursor,
                            state.high_water_timestamp,
                        )
                    )
                    continue
                request = ReviewPageRequest(
                    AppId(appid.value),
                    state.review_type,
                    state.cursor,
                    language,
                    cast(PurchaseType, purchase_type),
                    cast(ReviewFilter, review_filter),
                )
                try:
                    page = _retry_page(source, request, limits, sleep)
                except ExternalError as exc:
                    update_stream_state(
                        database,
                        {
                            "project_id": str(project_id),
                            "appid": appid.value,
                            "review_type": state.review_type,
                            "cursor": state.cursor,
                            "status": "failed",
                            "stop_reason": f"retry_exhausted:{exc.code}",
                        },
                    )
                    results.append(
                        StreamResult(
                            state.review_type,
                            "failed",
                            f"retry_exhausted:{exc.code}",
                            state.pages,
                            state.reviews,
                            state.cursor,
                            state.high_water_timestamp,
                        )
                    )
                    continue
                if page_hook is not None:
                    page_hook(page)
                next_cursor = page.cursor
                if not page.reviews:
                    reason = "empty_page"
                elif next_cursor == state.cursor or next_cursor in {"", "*"}:
                    reason = (
                        "repeated_cursor"
                        if next_cursor == state.cursor
                        else "non_progressing_cursor"
                    )
                elif (
                    incremental
                    and state.previous_high_water is not None
                    and _fully_known_overlap(
                        database,
                        project_id,
                        appid,
                        page,
                        state.previous_high_water,
                        limits.overlap_seconds,
                    )
                ):
                    reason = "fully_known_overlap"
                else:
                    reason = ""
                page_id = str(uuid4())
                records = [
                    _review_record(project_id, run_id, appid, state.review_type, review)
                    for review in page.reviews
                ]
                state.pages += 1
                state.reviews += len(records)
                state.high_water_timestamp = max(
                    (r.timestamp_updated for r in page.reviews),
                    default=state.high_water_timestamp,
                )
                if reason:
                    state.cursor = next_cursor
                    persist_page_checkpoint(
                        database,
                        page_values={
                            "id": page_id,
                            "project_id": str(project_id),
                            "appid": appid.value,
                            "review_type": state.review_type,
                            "request_json": json.dumps(
                                request.query_params(), sort_keys=True
                            ),
                            "cursor_in": request.cursor,
                            "cursor_out": next_cursor,
                            "http_status": 200,
                            "fetched_at": _now(),
                            "review_count": len(records),
                            "response_hash": source_hash(
                                [r.raw_json for r in page.reviews]
                            ),
                        },
                        reviews=records,
                        stream_values={
                            "project_id": str(project_id),
                            "appid": appid.value,
                            "review_type": state.review_type,
                            "cursor": state.cursor,
                            "high_water_timestamp": state.high_water_timestamp,
                            "status": "complete",
                            "stop_reason": reason,
                            "last_page_id": page_id,
                        },
                    )
                    results.append(
                        StreamResult(
                            state.review_type,
                            "complete",
                            reason,
                            state.pages,
                            state.reviews,
                            state.cursor,
                            state.high_water_timestamp,
                        )
                    )
                else:
                    state.cursor = next_cursor
                    persist_page_checkpoint(
                        database,
                        page_values={
                            "id": page_id,
                            "project_id": str(project_id),
                            "appid": appid.value,
                            "review_type": state.review_type,
                            "request_json": json.dumps(
                                request.query_params(), sort_keys=True
                            ),
                            "cursor_in": request.cursor,
                            "cursor_out": next_cursor,
                            "http_status": 200,
                            "fetched_at": _now(),
                            "review_count": len(records),
                            "response_hash": source_hash(
                                [r.raw_json for r in page.reviews]
                            ),
                        },
                        reviews=records,
                        stream_values={
                            "project_id": str(project_id),
                            "appid": appid.value,
                            "review_type": state.review_type,
                            "cursor": state.cursor,
                            "high_water_timestamp": state.previous_high_water
                            if incremental
                            else state.high_water_timestamp,
                            "status": "partial",
                            "stop_reason": None,
                            "last_page_id": page_id,
                        },
                    )
                    next_active.append(state)
            active = next_active
    except CrawlCancelled:
        cancelled = True
        for state in active:
            update_stream_state(
                database,
                {
                    "project_id": str(project_id),
                    "appid": appid.value,
                    "review_type": state.review_type,
                    "cursor": state.cursor,
                    "high_water_timestamp": state.previous_high_water,
                    "status": "partial",
                    "stop_reason": "cancelled",
                },
            )
            results.append(
                StreamResult(
                    state.review_type,
                    "partial",
                    "cancelled",
                    state.pages,
                    state.reviews,
                    state.cursor,
                    state.high_water_timestamp,
                )
            )
    return CrawlResult(tuple(results), cancelled)
