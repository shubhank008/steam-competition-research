"""Project and Steam app parsing tests."""

import pytest

from steam_research.projects import ProjectError, canonical_store_url, parse_app_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123", 123),
        ("https://store.steampowered.com/app/123/Example/", 123),
        ("https://steampowered.com/app/456", 456),
    ],
)
def test_parse_app_id_accepts_numeric_ids_and_store_urls(
    value: str, expected: int
) -> None:
    assert parse_app_id(value) == expected
    assert canonical_store_url(expected).endswith(f"/{expected}/")


@pytest.mark.parametrize(
    "value", ["0", "-1", "not-an-app", "https://example.com/app/1"]
)
def test_parse_app_id_rejects_invalid_input(value: str) -> None:
    with pytest.raises(ProjectError, match="positive app ID or Steam store URL"):
        parse_app_id(value)
