"""CLI integration tests using temporary projects and real SQLite paths."""

from pathlib import Path

from typer.testing import CliRunner

from steam_research.cli import app

runner = CliRunner()


def test_init_add_duplicate_invalid_and_list(tmp_path: Path) -> None:
    project = tmp_path / "market"

    initialized = runner.invoke(app, ["init", str(project), "--name", "Market"])
    assert initialized.exit_code == 0, initialized.output
    assert (project / "project.toml").is_file()
    assert (project / "project.sqlite3").is_file()

    added = runner.invoke(app, ["app", "add", "123", "--project", str(project)])
    assert added.exit_code == 0, added.output
    assert "Added competitor 123" in added.output

    duplicate = runner.invoke(
        app,
        [
            "app",
            "add",
            "https://store.steampowered.com/app/123/",
            "--project",
            str(project),
        ],
    )
    assert duplicate.exit_code == 0, duplicate.output
    assert "Already present competitor 123" in duplicate.output

    invalid = runner.invoke(app, ["app", "add", "bad", "--project", str(project)])
    assert invalid.exit_code != 0
    assert "positive app ID or Steam store URL" in invalid.output

    listing = runner.invoke(app, ["app", "list", "--project", str(project)])
    assert listing.exit_code == 0, listing.output
    assert listing.output.count("123\thttps://store.steampowered.com/app/123/") == 1
