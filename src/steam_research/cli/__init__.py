"""Command-line interface for Steam competitor research."""

from pathlib import Path
from typing import Annotated

import typer

from steam_research import __version__
from steam_research.projects import (
    ProjectError,
    add_competitor,
    initialize_project,
    list_competitors,
    project_paths,
)
from steam_research.storage import Database, status_json, status_view

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Collect and analyze Steam competitor research.",
)
app_commands = typer.Typer(add_completion=False, help="Manage project competitors.")
app.add_typer(app_commands, name="app")


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


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Directory for the new project.")] = Path(
        "."
    ),
    name: Annotated[str | None, typer.Option("--name", help="Project name.")] = None,
) -> None:
    """Initialize a project directory and SQLite database."""
    project_name = name or path.expanduser().resolve().name
    try:
        project = initialize_project(path, project_name)
    except ProjectError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Initialized project {project.name} at {project.paths.root}")


@app_commands.command("add")
def app_add(
    value: Annotated[str, typer.Argument(help="Steam app ID or store URL.")],
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
) -> None:
    """Add a Steam competitor to the project."""
    try:
        with Database(project_paths(project).database) as database:
            database.migrate()
            competitor, added = add_competitor(database, value)
    except (ProjectError, FileNotFoundError) as error:
        raise typer.BadParameter(str(error)) from error
    state = "Added" if added else "Already present"
    typer.echo(f"{state} competitor {competitor.appid}: {competitor.store_url}")


@app_commands.command("list")
def app_list(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
) -> None:
    """List competitors in the project."""
    try:
        with Database(project_paths(project).database) as database:
            database.migrate()
            competitors = list_competitors(database)
    except (ProjectError, FileNotFoundError) as error:
        raise typer.BadParameter(str(error)) from error
    for competitor in competitors:
        typer.echo(f"{competitor.appid}\t{competitor.store_url}")


@app.command()
def status(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show persisted pipeline runs and child work units."""
    try:
        with Database(project_paths(project).database) as database:
            database.migrate()
            if as_json:
                typer.echo(status_json(database))
                return
            for run in status_view(database):
                typer.echo(f"{run.run_type}\t{run.status}\t{run.id}")
                for unit in run.units:
                    typer.echo(
                        f"  {unit['unit_key']}\t{unit['status']}\t"
                        f"attempt={unit['attempt']}"
                    )
    except (ProjectError, FileNotFoundError) as error:
        raise typer.BadParameter(str(error)) from error


if __name__ == "__main__":
    app()
