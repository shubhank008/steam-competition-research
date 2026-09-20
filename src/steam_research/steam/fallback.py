"""Explicit browser fallback policy for Steam store pages."""

from __future__ import annotations

from typing import Any, Protocol

from steam_research.domain import ExternalError, ExternalErrorCode
from steam_research.steam.contracts import StorePageRequest
from steam_research.steam.store import FetchedDocument, StorePageFetcher


class BrowserPage(Protocol):
    def goto(self, url: str, *, timeout: float) -> Any: ...
    def content(self) -> str: ...
    def close(self) -> Any: ...


class BrowserContext(Protocol):
    def new_page(self) -> BrowserPage: ...
    def close(self) -> Any: ...


class BrowserFactory(Protocol):
    def open(self, *, timeout: float) -> BrowserContext: ...


def should_fallback(
    document: FetchedDocument | None = None, error: Exception | None = None
) -> bool:
    """Return true only for transport denial or known challenge pages."""
    if error is not None:
        return isinstance(error, ExternalError) and error.code in {
            ExternalErrorCode.AUTHENTICATION,
            ExternalErrorCode.CONNECTION,
            ExternalErrorCode.TIMEOUT,
        }
    if document is None:
        return False
    text = document.body.decode("utf-8", errors="replace").lower()
    return document.status_code in {401, 403, 429} or any(
        marker in text
        for marker in ("captcha", "cloudflare", "just a moment", "verify you are human")
    )


class PatchrightStorePageFetcher:
    """Optional Patchright boundary; browser objects always close."""

    def __init__(self, factory: BrowserFactory) -> None:
        self._factory = factory

    def fetch(self, request: StorePageRequest) -> FetchedDocument:
        context: BrowserContext | None = None
        page: BrowserPage | None = None
        try:
            context = self._factory.open(timeout=request.timeout_seconds)
            page = context.new_page()
            url = f"https://store.steampowered.com/app/{request.appid.value}/?cc={request.country_code}&l={request.language}"
            page.goto(url, timeout=request.timeout_seconds)
            return FetchedDocument(
                request.appid,
                200,
                url,
                page.content().encode(),
                {},
                "patchright",
                "",
                {"country_code": request.country_code, "language": request.language},
            )
        finally:
            if page is not None:
                page.close()
            if context is not None:
                context.close()


class FallbackStorePageFetcher:
    """Run HTTP first, retrying in a browser only for explicit triggers."""

    def __init__(self, http: StorePageFetcher, browser: StorePageFetcher) -> None:
        self._http = http
        self._browser = browser

    def fetch(self, request: StorePageRequest) -> FetchedDocument:
        try:
            document = self._http.fetch(request)
        except Exception as error:
            if not should_fallback(error=error):
                raise
            return self._browser.fetch(request)
        if should_fallback(document):
            return self._browser.fetch(request)
        return document
