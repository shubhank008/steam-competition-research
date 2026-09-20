"""Command-line interface for Steam competitor research."""

from typing import Annotated

import typer

from steam_research import __version__

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Collect and analyze Steam competitor research.",
)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the installed package version."),
    ] = False,
) -> None:
    """Provide the Steam competitor research command-line interface."""
    if version:
        typer.echo(__version__)


if __name__ == "__main__":
    app()
