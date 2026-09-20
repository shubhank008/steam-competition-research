"""Command-line interface for Steam competitor research."""

from pathlib import Path
from typing import Annotated

import typer

from steam_research import __version__
from steam_research.pipeline import (
    ProjectContext,
    aggregate_stage,
    classify_stage,
    crawl_reviews_stage,
    crawl_store,
    export_data,
    export_report,
    open_project,
    synthesize_stage,
)
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
crawl_commands = typer.Typer(
    add_completion=False, help="Collect Steam metadata and reviews."
)
export_commands = typer.Typer(
    add_completion=False, help="Export derived research outputs."
)
app.add_typer(app_commands, name="app")
app.add_typer(crawl_commands, name="crawl")
app.add_typer(export_commands, name="export")


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


def _context(project: Path) -> ProjectContext:
    try:
        return open_project(project)
    except Exception as error:
        raise typer.BadParameter(str(error)) from error


def _selected_appids(appid: int | None, all_apps: bool) -> tuple[int, ...] | None:
    if appid is not None and all_apps:
        raise typer.BadParameter("use either --app or --all")
    return (appid,) if appid is not None else None


@crawl_commands.command("store")
def crawl_store_command(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
    appid: Annotated[int | None, typer.Option("--app", help="Steam app ID.")] = None,
    all_apps: Annotated[
        bool, typer.Option("--all", help="Process every competitor.")
    ] = False,
) -> None:
    """Fetch and persist localized store metadata."""
    context = _context(project)
    try:
        run_id = crawl_store(context, _selected_appids(appid, all_apps))
    except Exception as error:
        typer.echo(f"store crawl failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"store_crawl\tcompleted\t{run_id}")


@crawl_commands.command("reviews")
def crawl_reviews_command(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
    appid: Annotated[int | None, typer.Option("--app", help="Steam app ID.")] = None,
    all_apps: Annotated[
        bool, typer.Option("--all", help="Process every competitor.")
    ] = False,
) -> None:
    """Crawl resumable positive and negative review streams."""
    context = _context(project)
    try:
        run_id = crawl_reviews_stage(context, _selected_appids(appid, all_apps))
    except Exception as error:
        typer.echo(f"review crawl failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"review_crawl\tcompleted\t{run_id}")


@app.command()
def classify(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
    scope: Annotated[
        str, typer.Option("--scope", help="Classification scope.")
    ] = "unclassified-only",
    seed: Annotated[int, typer.Option("--seed", help="Stable sampling seed.")] = 0,
) -> None:
    """Classify eligible reviews with the configured Stage 1 provider."""
    context = _context(project)
    try:
        run_id = classify_stage(context, scope=scope, seed=seed)  # type: ignore[arg-type]
    except Exception as error:
        typer.echo(f"classification failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"classification\tcompleted\t{run_id}")


@app.command("aggregate")
def aggregate_command(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
) -> None:
    """Compute deterministic metrics from current classified reviews."""
    context = _context(project)
    try:
        run_id = aggregate_stage(context)
    except Exception as error:
        typer.echo(f"aggregation failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"aggregation\tcompleted\t{run_id}")


@app.command("synthesize")
def synthesize_command(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
) -> None:
    """Generate bounded structured strategy output."""
    context = _context(project)
    try:
        run_id = synthesize_stage(context)
    except Exception as error:
        typer.echo(f"synthesis failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"synthesis\tcompleted\t{run_id}")


@app.command()
def run(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
    scope: Annotated[
        str, typer.Option("--scope", help="Classification scope.")
    ] = "unclassified-only",
) -> None:
    """Run collection, classification, aggregation, and synthesis in order."""
    context = _context(project)
    try:
        crawl_store(context)
        crawl_reviews_stage(context)
        classify_stage(context, scope=scope)  # type: ignore[arg-type]
        aggregate_stage(context)
        run_id = synthesize_stage(context)
    except Exception as error:
        typer.echo(f"pipeline failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"run\tcompleted\t{run_id}")


@export_commands.command("parquet")
def export_parquet_command(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
    output: Annotated[Path, typer.Option("--output", help="Output directory.")] = Path(
        "exports"
    ),
) -> None:
    """Export bounded analytical datasets to Parquet."""
    context = _context(project)
    try:
        result = export_data(context, output)
    except Exception as error:
        typer.echo(f"Parquet export failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"export\tcompleted\t{result.manifest_path}")


@export_commands.command("report")
def export_report_command(
    project: Annotated[
        Path, typer.Option("--project", help="Project directory.")
    ] = Path("."),
    output: Annotated[
        Path, typer.Option("--output", help="Markdown output path.")
    ] = Path("strategy-brief.md"),
) -> None:
    """Render the latest validated synthesis as Markdown."""
    context = _context(project)
    try:
        path = export_report(context, output)
    except Exception as error:
        typer.echo(f"report export failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"report\tcompleted\t{path}")


if __name__ == "__main__":
    app()
