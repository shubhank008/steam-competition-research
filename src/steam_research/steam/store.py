"""Steam store metadata fetching and parsing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from typing import Any, Protocol

from curl_cffi import requests

from steam_research.domain import AppId, ExternalError, ExternalErrorCode
from steam_research.steam.contracts import StorePageRequest, source_hash

STORE_URL = "https://store.steampowered.com/app/{appid}/"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"
PARSER_SCHEMA_VERSION = "store-v1"


@dataclass(frozen=True)
class FetchedDocument:
    appid: AppId
    status_code: int
    source_url: str
    body: bytes
    headers: Mapping[str, str]
    adapter: str
    fetched_at: str
    diagnostics: Mapping[str, str] = field(default_factory=dict)


class StorePageFetcher(Protocol):
    def fetch(self, request: StorePageRequest) -> FetchedDocument: ...


class StorePageParser(Protocol):
    def parse(
        self, document: FetchedDocument, *, structured: Mapping[str, Any] | None = None
    ) -> StorePageSnapshot: ...


@dataclass(frozen=True)
class Screenshot:
    full_url: str
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class ReleaseDate:
    raw: str | None
    normalized_date: str | None


@dataclass(frozen=True)
class Price:
    display: str | None
    initial_minor: int | None
    final_minor: int | None
    discount_percent: int | None
    currency: str | None


@dataclass(frozen=True)
class DescriptionSection:
    heading: str | None
    text: str
    html: str | None = None


@dataclass(frozen=True)
class MoreLikeThis:
    appid: int | None
    title: str | None
    url: str


@dataclass(frozen=True)
class StorePageSnapshot:
    appid: AppId
    source_url: str
    country_code: str
    store_language: str
    currency: str | None
    fetched_at: str
    fetch_adapter: str
    parser_schema_version: str
    title: str | None
    description_short: str | None
    caption_image_url: str | None
    screenshots: tuple[Screenshot, ...]
    total_reviews: int | None
    review_summary: str | None
    release_date: ReleaseDate
    developers: tuple[str, ...]
    publishers: tuple[str, ...]
    popular_tags: tuple[str, ...]
    price: Price
    description_sections: tuple[DescriptionSection, ...]
    description_full: str | None
    more_like_this: tuple[MoreLikeThis, ...]
    supported_languages: tuple[str, ...]
    platforms: tuple[str, ...]
    extraction_warnings: tuple[str, ...]
    field_provenance: Mapping[str, str]
    source_content_hash: str


class CurlCffiStorePageFetcher:
    """Fetch the localized HTML page and appdetails JSON independently."""

    def __init__(self, transport: Any | None = None) -> None:
        self._session = transport or requests.Session(impersonate="chrome")

    def fetch(self, request: StorePageRequest) -> FetchedDocument:
        url = STORE_URL.format(appid=request.appid.value)
        params = {"cc": request.country_code, "l": request.language}
        try:
            response = self._session.get(
                url, params=params, timeout=request.timeout_seconds
            )
        except requests.RequestsError as exc:
            raise ExternalError(
                ExternalErrorCode.CONNECTION, "Steam store connection failed"
            ) from exc
        if response.status_code in {401, 403}:
            raise ExternalError(
                ExternalErrorCode.AUTHENTICATION, "Steam store request denied"
            )
        if response.status_code >= 400:
            code = (
                ExternalErrorCode.SERVER
                if response.status_code >= 500
                else ExternalErrorCode.INVALID_REQUEST
            )
            raise ExternalError(
                code, f"Steam store request returned HTTP {response.status_code}"
            )
        return FetchedDocument(
            request.appid,
            response.status_code,
            str(response.url),
            response.content,
            dict(response.headers),
            "curl_cffi",
            datetime.now(UTC).isoformat(),
            {"country_code": request.country_code, "language": request.language},
        )

    def fetch_structured(self, request: StorePageRequest) -> Mapping[str, Any] | None:
        try:
            response = self._session.get(
                DETAILS_URL,
                params={
                    "appids": request.appid.value,
                    "cc": request.country_code,
                    "l": request.language,
                },
                timeout=request.timeout_seconds,
            )
            payload = response.json()
            item = payload.get(str(request.appid.value), {})
            return item.get("data") if item.get("success") is True else None
        except (requests.RequestsError, ValueError, TypeError, AttributeError):
            return None


class StoreMetadataParser:
    """Prefer appdetails values, then recover missing fields from page HTML."""

    def parse(
        self, document: FetchedDocument, *, structured: Mapping[str, Any] | None = None
    ) -> StorePageSnapshot:
        html = document.body.decode("utf-8", errors="replace")
        data = dict(structured or {})
        warnings: list[str] = []
        provenance: dict[str, str] = {}

        def value(field: str, fallback: str | None = None) -> Any:
            if data.get(field) not in (None, "", []):
                provenance[field] = "appdetails"
                return data[field]
            if fallback not in (None, "", []):
                provenance[field] = "html"
                return fallback
            warnings.append(f"missing_optional:{field}")
            return None

        title = value("title", data.get("name") or _meta(html, "og:title"))
        short = value(
            "description_short",
            data.get("short_description")
            or _class_text(html, "game_description_snippet"),
        )
        header = value(
            "caption_image_url", data.get("header_image") or _meta(html, "og:image")
        )
        for field_name, structured_name in (
            ("title", "name"),
            ("description_short", "short_description"),
            ("caption_image_url", "header_image"),
        ):
            if data.get(structured_name) not in (None, "", []):
                provenance[field_name] = "appdetails"
        developers = tuple(str(x) for x in data.get("developers", []))
        publishers = tuple(str(x) for x in data.get("publishers", []))
        for field_name, items in (
            ("developers", developers),
            ("publishers", publishers),
        ):
            if items:
                provenance[field_name] = "appdetails"
            else:
                warnings.append(f"missing_optional:{field_name}")
        screenshots = tuple(
            Screenshot(str(x.get("path_full", "")), x.get("path_thumbnail"))
            for x in data.get("screenshots", [])
            if isinstance(x, dict) and x.get("path_full")
        )
        if screenshots:
            provenance["screenshots"] = "appdetails"
        else:
            warnings.append("missing_optional:screenshots")
        genres = tuple(
            str(x.get("description", ""))
            for x in data.get("genres", [])
            if isinstance(x, dict) and x.get("description")
        )
        categories = tuple(
            str(x.get("description", ""))
            for x in data.get("categories", [])
            if isinstance(x, dict) and x.get("description")
        )
        tags = (
            tuple(str(x) for x in data.get("categories", []) if isinstance(x, str))
            or genres + categories
        )
        price_data = data.get("price_overview") or {}
        price = Price(
            price_data.get("final_formatted"),
            _int(price_data.get("initial")),
            _int(price_data.get("final")),
            _int(price_data.get("discount_percent")),
            price_data.get("currency"),
        )
        if price.display is None:
            warnings.append("missing_optional:price")
        release = data.get("release_date") or {}
        raw_date = release.get("date") if isinstance(release, dict) else None
        description = value(
            "detailed_description", _class_text(html, "game_area_description")
        )
        if description is not None:
            description = _clean_html(description)
        platforms = tuple(
            k for k in ("windows", "mac", "linux") if data.get("platforms", {}).get(k)
        )
        languages = tuple(
            x.strip()
            for x in str(data.get("supported_languages", "")).split(",")
            if x.strip()
        )
        review_summary = _class_text(html, "user_reviews_summary_row")
        if review_summary:
            provenance["review_summary"] = "html"
        else:
            warnings.append("missing_optional:review_summary")
        return StorePageSnapshot(
            document.appid,
            document.source_url,
            document.diagnostics.get("country_code", "US"),
            document.diagnostics.get("language", "english"),
            price.currency,
            document.fetched_at,
            document.adapter,
            PARSER_SCHEMA_VERSION,
            title,
            short,
            header,
            screenshots,
            _int(data.get("reviews")),
            review_summary,
            ReleaseDate(raw_date, _normalize_date(raw_date)),
            developers,
            publishers,
            tags,
            price,
            (DescriptionSection(None, description),) if description else (),
            description,
            (),
            languages,
            platforms,
            tuple(dict.fromkeys(warnings)),
            provenance,
            source_hash(document.body),
        )


def _meta(html: str, name: str) -> str | None:
    match = re.search(
        rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)',
        html,
        re.I,
    )
    return unescape(match.group(1)).strip() if match else None


def _class_text(html: str, class_name: str) -> str | None:
    match = re.search(
        rf'<[^>]+class=["\'][^"\']*{re.escape(class_name)}[^"\']*["\'][^>]*>(.*?)</',
        html,
        re.I | re.S,
    )
    return _clean_html(match.group(1)) if match else None


def _clean_html(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{1,2})\s+\w+[,]?\s+(\d{4})", value)
    if not match:
        return None
    months = {
        m.lower(): i
        for i, m in enumerate(
            (
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            ),
            1,
        )
    }
    month = next((n for name, n in months.items() if name[:3] in value.lower()), None)
    return f"{match.group(2)}-{month:02d}-{int(match.group(1)):02d}" if month else None
