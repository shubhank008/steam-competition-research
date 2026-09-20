"""Canonical review persistence, lineage, and query services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from steam_research.storage.database import Database

UpsertKind = Literal["new", "changed", "unchanged"]


@dataclass(frozen=True)
class ReviewRecord:
    project_id: UUID | str
    appid: int
    recommendationid: str
    language: str
    voted_up: bool
    votes_up: int
    votes_funny: int
    weighted_vote_score: float | None
    comment_count: int
    steam_purchase: bool
    received_for_free: bool
    refunded: bool
    written_during_early_access: bool
    primarily_steam_deck: bool | None
    playtime_at_review: int | None
    playtime_forever: int | None
    playtime_last_two_weeks: int | None
    last_played: int | None
    timestamp_created: int
    timestamp_updated: int
    review_text: str
    source_review_type: str
    source_hash: str
    raw_json: str
    source_run_id: UUID | str


@dataclass(frozen=True)
class UpsertCounts:
    new: int = 0
    changed: int = 0
    unchanged: int = 0

    def add(self, kind: UpsertKind) -> UpsertCounts:
        return UpsertCounts(
            self.new + (kind == "new"),
            self.changed + (kind == "changed"),
            self.unchanged + (kind == "unchanged"),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _upsert_review(connection: Any, review: ReviewRecord) -> UpsertKind:
    project_id = str(review.project_id)
    source_run_id = str(review.source_run_id)
    existing = connection.execute(
        "SELECT source_hash FROM reviews WHERE project_id = ? AND appid = ? "
        "AND recommendationid = ?",
        (project_id, review.appid, review.recommendationid),
    ).fetchone()
    kind: UpsertKind = (
        "new"
        if existing is None
        else ("unchanged" if existing[0] == review.source_hash else "changed")
    )
    now = _now()
    values = (
        project_id,
        review.appid,
        review.recommendationid,
        review.language,
        int(review.voted_up),
        review.votes_up,
        review.votes_funny,
        review.weighted_vote_score,
        review.comment_count,
        int(review.steam_purchase),
        int(review.received_for_free),
        int(review.refunded),
        int(review.written_during_early_access),
        None
        if review.primarily_steam_deck is None
        else int(review.primarily_steam_deck),
        review.playtime_at_review,
        review.playtime_forever,
        review.playtime_last_two_weeks,
        review.last_played,
        review.timestamp_created,
        review.timestamp_updated,
        review.review_text,
        review.source_review_type,
        review.source_hash,
        review.raw_json,
        now,
        now,
        source_run_id,
    )
    connection.execute(
        "INSERT INTO reviews (project_id, appid, recommendationid, language, voted_up, "
        "votes_up, votes_funny, weighted_vote_score, comment_count, steam_purchase, "
        "received_for_free, refunded, written_during_early_access, "
        "primarily_steam_deck, "
        "playtime_at_review, playtime_forever, playtime_last_two_weeks, "
        "last_played, "
        "timestamp_created, timestamp_updated, review_text, "
        "source_review_type, source_hash, "
        "raw_json, first_seen_at, last_seen_at, last_source_run_id) "  # noqa: E501
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "  # noqa: E501
        "ON CONFLICT(project_id, appid, recommendationid) DO UPDATE SET "  # noqa: E501
        "language=excluded.language, voted_up=excluded.voted_up, votes_up=excluded.votes_up, "  # noqa: E501
        "votes_funny=excluded.votes_funny, weighted_vote_score=excluded.weighted_vote_score, "  # noqa: E501
        "comment_count=excluded.comment_count, steam_purchase=excluded.steam_purchase, "  # noqa: E501
        "received_for_free=excluded.received_for_free, refunded=excluded.refunded, "  # noqa: E501
        "written_during_early_access=excluded.written_during_early_access, "  # noqa: E501
        "primarily_steam_deck=excluded.primarily_steam_deck, playtime_at_review=excluded.playtime_at_review, "  # noqa: E501
        "playtime_forever=excluded.playtime_forever, playtime_last_two_weeks=excluded.playtime_last_two_weeks, "  # noqa: E501
        "last_played=excluded.last_played, timestamp_created=excluded.timestamp_created, "  # noqa: E501
        "timestamp_updated=excluded.timestamp_updated, review_text=excluded.review_text, "  # noqa: E501
        "source_review_type=excluded.source_review_type, source_hash=excluded.source_hash, "  # noqa: E501
        "raw_json=excluded.raw_json, last_seen_at=excluded.last_seen_at, "  # noqa: E501
        "last_source_run_id=excluded.last_source_run_id",
        values,
    )
    return kind


def upsert_review(database: Database, review: ReviewRecord) -> UpsertKind:
    """Insert or update one review, retaining the caller's raw JSON exactly."""
    with database.transaction() as connection:
        return _upsert_review(connection, review)


def upsert_reviews(database: Database, reviews: list[ReviewRecord]) -> UpsertCounts:
    """Upsert a batch and report new, changed, and unchanged source objects."""
    counts = UpsertCounts()
    with database.transaction() as connection:
        for review in reviews:
            counts = counts.add(_upsert_review(connection, review))
    return counts


def query_reviews(
    database: Database,
    *,
    project_id: UUID | str,
    appid: int | None = None,
    language: str | None = None,
    voted_up: bool | None = None,
    created_after: int | None = None,
    created_before: int | None = None,
    playtime_min: int | None = None,
    playtime_max: int | None = None,
) -> list[dict[str, Any]]:
    """Query normalized review facts with composable, parameterized filters."""
    clauses = ["project_id = ?"]
    parameters: list[Any] = [str(project_id)]
    filters = (("appid", appid), ("language", language))
    for column, value in filters:
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    if voted_up is not None:
        clauses.append("voted_up = ?")
        parameters.append(int(voted_up))
    for column, operator, value in (
        ("timestamp_created", ">=", created_after),
        ("timestamp_created", "<=", created_before),
        ("playtime_at_review", ">=", playtime_min),
        ("playtime_at_review", "<=", playtime_max),
    ):
        if value is not None:
            clauses.append(f"{column} {operator} ?")
            parameters.append(value)
    rows = database.connection.execute(
        "SELECT * FROM reviews WHERE " + " AND ".join(clauses) + " "
        "ORDER BY timestamp_created, recommendationid",
        parameters,
    ).fetchall()
    return [dict(row) for row in rows]


def record_api_page(database: Database, values: dict[str, Any]) -> None:
    """Persist API request metadata and optional response bytes metadata."""
    columns = (
        "id",
        "project_id",
        "appid",
        "review_type",
        "request_json",
        "cursor_in",
        "cursor_out",
        "http_status",
        "fetched_at",
        "review_count",
        "response_hash",
        "response_bytes",
    )
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO review_api_pages ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")",
            tuple(values.get(c) for c in columns),
        )


def update_stream_state(database: Database, values: dict[str, Any]) -> None:
    """Upsert durable independent positive/negative stream state."""
    columns = (
        "project_id",
        "appid",
        "review_type",
        "cursor",
        "high_water_timestamp",
        "status",
        "stop_reason",
        "last_page_id",
        "updated_at",
    )
    values = {**values, "updated_at": values.get("updated_at", _now())}
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO review_stream_state ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ") ON CONFLICT(project_id, appid, review_type) "
            "DO UPDATE SET cursor=excluded.cursor, "
            "high_water_timestamp=excluded.high_water_timestamp, "
            "status=excluded.status, stop_reason=excluded.stop_reason, "
            "last_page_id=excluded.last_page_id, updated_at=excluded.updated_at",
            tuple(values.get(c) for c in columns),
        )


def persist_page_checkpoint(
    database: Database,
    *,
    page_values: dict[str, Any],
    reviews: list[ReviewRecord],
    stream_values: dict[str, Any],
) -> UpsertCounts:
    """Commit page lineage, reviews, and the next cursor atomically."""
    counts = UpsertCounts()
    columns = (
        "id",
        "project_id",
        "appid",
        "review_type",
        "request_json",
        "cursor_in",
        "cursor_out",
        "http_status",
        "fetched_at",
        "review_count",
        "response_hash",
        "response_bytes",
    )
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO review_api_pages ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")",
            tuple(page_values.get(column) for column in columns),
        )
        for review in reviews:
            counts = counts.add(_upsert_review(connection, review))
        state_columns = (
            "project_id",
            "appid",
            "review_type",
            "cursor",
            "high_water_timestamp",
            "status",
            "stop_reason",
            "last_page_id",
            "updated_at",
        )
        state = {**stream_values, "updated_at": stream_values.get("updated_at", _now())}
        connection.execute(
            "INSERT INTO review_stream_state ("
            + ", ".join(state_columns)
            + ") VALUES ("
            + ", ".join("?" for _ in state_columns)
            + ") ON CONFLICT(project_id, appid, review_type) DO UPDATE SET "
            "cursor=excluded.cursor, "
            "high_water_timestamp=excluded.high_water_timestamp, "
            "status=excluded.status, stop_reason=excluded.stop_reason, "
            "last_page_id=excluded.last_page_id, updated_at=excluded.updated_at",
            tuple(state.get(column) for column in state_columns),
        )
    return counts
