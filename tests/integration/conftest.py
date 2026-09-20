"""Fixtures for real SQLite integration tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from steam_research.storage import Database


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    with Database(tmp_path / "project.sqlite3") as database:
        yield database
