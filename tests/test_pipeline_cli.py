"""Offline end-to-end coverage for the public pipeline command surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steam_research.cli import app
from steam_research.storage import Database

runner = CliRunner()


def _fixture(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "apps": [
                    {
                        "appid": 101,
                        "name": "Alpha Fixture",
                        "reviews": [
                            {
                                "recommendationid": "a1",
                                "review": "Stable and enjoyable onboarding",
                                "voted_up": True,
                            }
                        ],
                    },
                    {
                        "appid": 202,
                        "name": "Beta Fixture",
                        "reviews": [
                            {
                                "recommendationid": "b1",
                                "review": "Crashes during loading",
                                "voted_up": False,
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_two_competitor_offline_cli_run_and_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    fixture = tmp_path / "fixture.json"
    _fixture(fixture)
    monkeypatch.setenv("STEAM_RESEARCH_COMPETITORS", "101,202")
    assert (
        runner.invoke(app, ["init", str(project), "--name", "Fixture Market"]).exit_code
        == 0
    )
    result = runner.invoke(
        app,
        ["run", "--project", str(project), "--offline-fixture", str(fixture)],
    )
    assert result.exit_code == 0, result.output

    status = runner.invoke(app, ["status", "--project", str(project), "--json"])
    assert status.exit_code == 0, status.output
    status_rows = json.loads(status.output)
    assert {row["run_type"] for row in status_rows} == {"store_crawl", "review_crawl"}
    assert all(row["status"] == "completed" for row in status_rows)
    with Database(project / "project.sqlite3") as database:
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM review_classifications WHERE status='success'"
            ).fetchone()[0]
            == 2
        )
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM synthesis_runs WHERE status='completed'"
            ).fetchone()[0]
            == 1
        )

    exports = tmp_path / "exports"
    result = runner.invoke(
        app,
        ["export", "parquet", "--project", str(project), "--output", str(exports)],
    )
    assert result.exit_code == 0, result.output
    assert len(tuple(exports.glob("*.parquet"))) == 5
    assert (exports / "manifest.json").is_file()

    report = tmp_path / "brief.md"
    result = runner.invoke(
        app,
        [
            "export",
            "report",
            "--project",
            str(project),
            "--output",
            str(report),
            "--min-words",
            "1",
            "--max-words",
            "300",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Prioritize reliable experiences" in report.read_text(encoding="utf-8")
