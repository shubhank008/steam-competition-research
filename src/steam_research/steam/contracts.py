"""Framework-independent contracts for Steam collection adapters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import quote

from steam_research.domain import AppId, DomainError, ExternalError, ExternalErrorCode

ReviewType = Literal["positive", "negative"]
PurchaseType = Literal["all", "steam", "purchase"]
ReviewFilter = Literal["recent", "updated", "all"]


class OffTopicPolicy(StrEnum):
    INCLUDE = "0"
    EXCLUDE = "1"


@dataclass(frozen=True)
class StorePageRequest:
    appid: AppId
    country_code: str = "US"
    language: str = "english"
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.country_code or not self.language:
            raise DomainError("country code and language must not be empty")
        if self.timeout_seconds <= 0:
            raise DomainError("timeout must be positive")


@dataclass(frozen=True)
class StorePageResponse:
    appid: AppId
    status_code: int
    source_url: str
    body: bytes
    headers: Mapping[str, str]


@dataclass(frozen=True)
class ReviewPageRequest:
    appid: AppId
    review_type: ReviewType
    cursor: str = "*"
    language: str = "all"
    purchase_type: PurchaseType = "all"
    review_filter: ReviewFilter = "all"
    off_topic_policy: OffTopicPolicy = OffTopicPolicy.INCLUDE
    timeout_seconds: float = 30.0
    page_size: int = 100

    def __post_init__(self) -> None:
        if not self.cursor:
            raise DomainError("review cursor must not be empty")
        if self.timeout_seconds <= 0:
            raise DomainError("timeout must be positive")
        if self.page_size != 100:
            raise DomainError("Steam review pages must request exactly 100 reviews")

    def query_params(self) -> dict[str, str | int]:
        return {
            "json": 1,
            "language": self.language,
            "cursor": self.cursor,
            "num_per_page": self.page_size,
            "review_type": self.review_type,
            "purchase_type": self.purchase_type,
            "filter": self.review_filter,
            "filter_offtopic_activity": self.off_topic_policy.value,
        }

    def encoded_cursor(self) -> str:
        return quote(self.cursor, safe="")


@dataclass(frozen=True)
class Review:
    recommendationid: str
    language: str
    review: str
    voted_up: bool
    votes_up: int
    votes_funny: int
    timestamp_created: int
    timestamp_updated: int
    steam_purchase: bool
    received_for_free: bool
    refunded: bool
    written_during_early_access: bool
    raw_json: str = "{}"
    source_hash: str = ""


@dataclass(frozen=True)
class ReviewPage:
    appid: AppId
    review_type: ReviewType
    cursor: str
    reviews: tuple[Review, ...]
    query_summary: Mapping[str, str | int]
    success: bool = True
    query_summary_hash: str = ""


def source_hash(value: bytes | str | Mapping[str, Any] | list[Any]) -> str:
    """Return a deterministic SHA-256 hash for source bytes or JSON values."""
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def classify_http_error(
    status_code: int, *, retry_after: float | None = None
) -> ExternalError | None:
    """Map HTTP outcomes to the shared adapter-neutral error taxonomy."""
    if 200 <= status_code < 300:
        return None
    if status_code == 429:
        return ExternalError(
            ExternalErrorCode.RATE_LIMITED, "Steam rate limit", retry_after
        )
    if status_code in {401, 403}:
        return ExternalError(ExternalErrorCode.AUTHENTICATION, "Steam request denied")
    if status_code == 404:
        return ExternalError(
            ExternalErrorCode.NOT_FOUND, "Steam app or endpoint not found"
        )
    if 500 <= status_code <= 599:
        return ExternalError(ExternalErrorCode.SERVER, "Steam server error")
    if 400 <= status_code <= 499:
        return ExternalError(
            ExternalErrorCode.INVALID_REQUEST, "Steam request rejected"
        )
    return ExternalError(ExternalErrorCode.UNKNOWN, "Unexpected Steam HTTP status")


def validate_review_payload(payload: Any, request: ReviewPageRequest) -> ReviewPage:
    """Validate and normalize a Steam review response without persisting raw text."""
    if not isinstance(payload, dict) or payload.get("success") != 1:
        raise ExternalError(
            ExternalErrorCode.INVALID_RESPONSE,
            "Steam review response failed schema validation",
        )
    cursor, reviews = payload.get("cursor"), payload.get("reviews")
    if not isinstance(cursor, str) or not isinstance(reviews, list):
        raise ExternalError(
            ExternalErrorCode.INVALID_RESPONSE,
            "Steam review response failed schema validation",
        )
    normalized: list[Review] = []
    required = (
        "recommendationid",
        "language",
        "review",
        "voted_up",
        "timestamp_created",
        "timestamp_updated",
    )
    for item in reviews:
        if not isinstance(item, dict) or any(key not in item for key in required):
            raise ExternalError(
                ExternalErrorCode.INVALID_RESPONSE,
                "Steam review item failed schema validation",
            )
        try:
            raw_json = json.dumps(item, sort_keys=True, separators=(",", ":"))
            normalized.append(
                Review(
                    str(item["recommendationid"]),
                    str(item["language"]),
                    str(item["review"]),
                    bool(item["voted_up"]),
                    int(item.get("votes_up", 0)),
                    int(item.get("votes_funny", 0)),
                    int(item["timestamp_created"]),
                    int(item["timestamp_updated"]),
                    bool(item.get("steam_purchase", False)),
                    bool(item.get("received_for_free", False)),
                    bool(item.get("refunded", False)),
                    bool(item.get("written_during_early_access", False)),
                    raw_json,
                    source_hash(raw_json),
                )
            )
        except (TypeError, ValueError):
            raise ExternalError(
                ExternalErrorCode.INVALID_RESPONSE,
                "Steam review item failed schema validation",
            ) from None
    return ReviewPage(
        request.appid,
        request.review_type,
        cursor,
        tuple(normalized),
        request.query_params(),
        query_summary_hash=source_hash(request.query_params()),
    )
