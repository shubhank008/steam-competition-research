from steam_research.domain import AppId, ExternalError, ExternalErrorCode
from steam_research.steam import (
    FallbackStorePageFetcher,
    PatchrightStorePageFetcher,
    StorePageRequest,
    should_fallback,
)
from steam_research.steam.store import FetchedDocument


class FakePage:
    def __init__(self, body: str = "<html>ok</html>", fail: bool = False) -> None:
        self.body, self.fail, self.closed = body, fail, False

    def goto(self, url: str, *, timeout: float) -> None:
        if self.fail:
            raise RuntimeError("navigation failed")

    def content(self) -> str:
        return self.body

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page, self.closed = page, False

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeFactory:
    def __init__(self, page: FakePage) -> None:
        self.context = FakeContext(page)

    def open(self, *, timeout: float) -> FakeContext:
        return self.context


def request() -> StorePageRequest:
    return StorePageRequest(AppId(10), country_code="DE", language="german")


def document(body: bytes = b"ok", status: int = 200) -> FetchedDocument:
    return FetchedDocument(AppId(10), status, "url", body, {}, "curl_cffi", "now")


def test_challenge_and_denial_trigger_but_parser_regression_does_not() -> None:
    assert should_fallback(document(b"<title>Just a moment...</title>"))
    assert should_fallback(document(status=403))
    assert not should_fallback(document(b"<html>changed markup</html>"))


def test_fallback_uses_browser_only_for_http_denial() -> None:
    class Http:
        def fetch(self, request: StorePageRequest) -> FetchedDocument:
            raise ExternalError(ExternalErrorCode.AUTHENTICATION, "denied")

    page = FakePage()
    result = FallbackStorePageFetcher(
        Http(), PatchrightStorePageFetcher(FakeFactory(page))
    ).fetch(request())
    assert result.adapter == "patchright"
    assert page.closed


def test_browser_closes_page_and_context_on_failure() -> None:
    page = FakePage(fail=True)
    factory = FakeFactory(page)
    try:
        PatchrightStorePageFetcher(factory).fetch(request())
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected navigation failure")
    assert page.closed and factory.context.closed
