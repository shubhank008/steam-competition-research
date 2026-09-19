"""Smoke tests for the installed command-line entry point."""

import subprocess


def test_cli_help_runs_through_installed_entry_point() -> None:
    result = subprocess.run(
        ["steam-research", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Collect and analyze Steam competitor research." in result.stdout
