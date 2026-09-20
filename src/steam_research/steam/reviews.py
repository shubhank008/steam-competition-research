"""Steam user-review API adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from curl_cffi import requests

from steam_research.domain import ExternalError, ExternalErrorCode
from steam_research.steam.contracts import (
    ReviewPage,
    ReviewPageRequest,
    classify_http_error,
    validate_review_payload,
)

REVIEW_ENDPOINT = "https://store.steampowered.com/appreviews/{appid}"


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class HttpTransport(Protocol):
    def get(
        self, url: str, *, params: Mapping[str, str | int], timeout: float
    ) -> HttpResponse: ...


class CurlCffiTransport:
    """Production HTTP boundary using curl_cffi's browser impersonation."""

    def __init__(self, *, impersonate: str = "chrome") -> None:
        self._session: requests.Session[Any] = requests.Session(impersonate=impersonate)

    def get(
        self, url: str, *, params: Mapping[str, str | int], timeout: float
    ) -> HttpResponse:
        response = self._session.get(url, params=dict(params), timeout=timeout)
        return HttpResponse(
            response.status_code, response.content, dict(response.headers)
        )


class SteamReviewApi:
    """Fetch and validate one Steam review page; crawling is a separate concern."""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport or CurlCffiTransport()

    def fetch_page(self, request: ReviewPageRequest) -> ReviewPage:
        url = REVIEW_ENDPOINT.format(appid=request.appid.value)
        try:
            response = self._transport.get(
                url, params=request.query_params(), timeout=request.timeout_seconds
            )
        except requests.RequestsError as exc:
            raise ExternalError(
                ExternalErrorCode.CONNECTION, "Steam connection failed"
            ) from exc

        retry_after = _retry_after_seconds(response.headers.get("retry-after"))
        error = classify_http_error(response.status_code, retry_after=retry_after)
        if error is not None:
            raise error
        try:
            payload: Any = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalError(
                ExternalErrorCode.INVALID_RESPONSE,
                "Steam review response was not valid JSON",
            ) from exc
        return validate_review_payload(payload, request)


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return max(0.0, (parsed - parsed.now(parsed.tzinfo)).total_seconds())
