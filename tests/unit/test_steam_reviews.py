"""Offline Steam review adapter tests using committed sanitized responses."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from steam_research.domain import AppId, ExternalError, ExternalErrorCode
from steam_research.steam import HttpResponse, ReviewPageRequest, SteamReviewApi

FIXTURES = Path(__file__).parent / "fixtures" / "steam"


class FixtureTransport:
    """External HTTP boundary fake; keeps adapter tests offline and deterministic."""

    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str | int], float]] = []

    def get(
        self, url: str, *, params: Mapping[str, str | int], timeout: float
    ) -> HttpResponse:
        self.calls.append((url, params, timeout))
        return self.response


def response(name: str, status: int = 200) -> HttpResponse:
    return HttpResponse(status, (FIXTURES / name).read_bytes(), {})


def test_adapter_fetches_positive_page_with_exact_parameters() -> None:
    transport = FixtureTransport(response("review_success.json"))
    request = ReviewPageRequest(AppId(480), "positive", cursor="cursor+with=special")
    page = SteamReviewApi(transport).fetch_page(request)
    assert page.review_type == "positive"
    assert transport.calls == [
        (
            "https://store.steampowered.com/appreviews/480",
            request.query_params(),
            30.0,
        )
    ]


def test_adapter_fetches_negative_stream_without_changing_request_contract() -> None:
    transport = FixtureTransport(response("review_success.json"))
    request = ReviewPageRequest(AppId(480), "negative", purchase_type="purchase")
    assert SteamReviewApi(transport).fetch_page(request).review_type == "negative"
    assert transport.calls[0][1]["review_type"] == "negative"
    assert transport.calls[0][1]["purchase_type"] == "purchase"


@pytest.mark.parametrize(
    ("status", "fixture", "code", "retryable"),
    [
        (429, "rate_limited.json", ExternalErrorCode.RATE_LIMITED, True),
        (404, "terminal_error.json", ExternalErrorCode.NOT_FOUND, False),
    ],
)
def test_adapter_maps_http_failures(
    status: int, fixture: str, code: ExternalErrorCode, retryable: bool
) -> None:
    with pytest.raises(ExternalError) as exc_info:
        SteamReviewApi(FixtureTransport(response(fixture, status))).fetch_page(
            ReviewPageRequest(AppId(480), "positive")
        )
    assert exc_info.value.code is code
    assert exc_info.value.retryable is retryable


def test_adapter_maps_server_error_without_network() -> None:
    with pytest.raises(ExternalError) as exc_info:
        SteamReviewApi(
            FixtureTransport(response("terminal_error.json", 503))
        ).fetch_page(ReviewPageRequest(AppId(480), "positive"))
    assert exc_info.value.code is ExternalErrorCode.SERVER
    assert exc_info.value.retryable


def test_adapter_rejects_malformed_json() -> None:
    transport = FixtureTransport(HttpResponse(200, b"not json", {}))
    with pytest.raises(ExternalError) as exc_info:
        SteamReviewApi(transport).fetch_page(ReviewPageRequest(AppId(480), "positive"))
    assert exc_info.value.code is ExternalErrorCode.INVALID_RESPONSE


def test_rate_limit_fixture_is_sanitized() -> None:
    assert json.loads((FIXTURES / "rate_limited.json").read_text())["status"] == 429
