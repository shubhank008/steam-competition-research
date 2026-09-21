"""CLI integration tests using temporary projects and real SQLite paths."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from steam_research.cli import app
from steam_research.pipeline import open_project

runner = CliRunner()


def test_init_uses_environment_competitors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "market"
    monkeypatch.setenv("STEAM_RESEARCH_COMPETITORS", "123, 456,123")
    initialized = runner.invoke(app, ["init", str(project), "--name", "Market"])
    assert initialized.exit_code == 0, initialized.output
    context = open_project(project)
    assert [item.appid for item in context.competitors] == [123, 456]


def test_pipeline_commands_are_exposed_and_status_json_is_machine_readable() -> None:
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    for command in ("crawl", "classify", "aggregate", "synthesize", "run", "export"):
        assert command in help_result.output

    status_result = runner.invoke(app, ["status", "--help"])
    assert status_result.exit_code == 0
    assert "--json" in status_result.output
