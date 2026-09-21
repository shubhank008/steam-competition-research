from pathlib import Path

import pytest

from steam_research.pipeline import crawl_store, open_project
from steam_research.projects import initialize_project
from steam_research.storage import Database


class InterruptingFetcher:
    def fetch(self, request: object) -> None:
        raise KeyboardInterrupt

    def fetch_structured(self, request: object) -> None:
        return None


def test_store_interrupt_is_persisted_as_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STEAM_RESEARCH_COMPETITORS", "101")
    project = initialize_project(tmp_path / "project", "Interrupted")
    context = open_project(project.paths.root)

    crawl_store(context, fetcher=InterruptingFetcher())

    with Database(project.paths.database) as database:
        run = database.connection.execute(
            "SELECT status, error_summary FROM runs WHERE run_type='store_crawl'"
        ).fetchone()
        unit = database.connection.execute(
            "SELECT status, error_summary FROM run_units"
        ).fetchone()
    assert tuple(run) == ("cancelled", "interrupted")
    assert tuple(unit) == ("cancelled", "interrupted")
