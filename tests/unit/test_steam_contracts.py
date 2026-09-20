"""Offline contract tests using sanitized Steam response fixtures."""

import json
from pathlib import Path

import pytest

from steam_research.domain import AppId, ExternalError, ExternalErrorCode
from steam_research.steam import (
    OffTopicPolicy,
    ReviewPageRequest,
    classify_http_error,
    source_hash,
    validate_review_payload,
)

FIXTURES = Path(__file__).parent / "fixtures" / "steam"


def load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def test_success_fixture_and_exact_request_parameters() -> None:
    request = ReviewPageRequest(
        AppId(10),
        "negative",
        cursor="a+b=c",
        purchase_type="steam",
        off_topic_policy=OffTopicPolicy.EXCLUDE,
    )
    page = validate_review_payload(load("review_success.json"), request)
    assert len(page.reviews) == 1
    assert request.query_params()["num_per_page"] == 100
    assert request.query_params()["purchase_type"] == "steam"
    assert request.query_params()["filter_offtopic_activity"] == "1"
    assert request.encoded_cursor() == "a%2Bb%3Dc"


@pytest.mark.parametrize(
    "fixture", ["review_missing_fields.json", "review_invalid_schema.json"]
)
def test_invalid_response_fixtures_are_terminal(fixture: str) -> None:
    request = ReviewPageRequest(AppId(10), "positive")
    with pytest.raises(ExternalError) as exc_info:
        validate_review_payload(load(fixture), request)
    assert exc_info.value.code is ExternalErrorCode.INVALID_RESPONSE
    assert not exc_info.value.retryable


def test_source_hash_is_stable_for_json_values() -> None:
    assert source_hash({"b": 2, "a": 1}) == source_hash({"a": 1, "b": 2})
    assert len(source_hash(b"fixture")) == 64


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, ExternalErrorCode.RATE_LIMITED, True),
        (404, ExternalErrorCode.NOT_FOUND, False),
        (503, ExternalErrorCode.SERVER, True),
    ],
)
def test_http_error_mapping(
    status: int, code: ExternalErrorCode, retryable: bool
) -> None:
    error = classify_http_error(status)
    assert error is not None
    assert error.code is code
    assert error.retryable is retryable
