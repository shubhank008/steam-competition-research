"""Command-line interface for Steam competitor research."""

from pathlib import Path
from typing import Annotated

import typer

from steam_research import __version__
from steam_research.llm import GenerationProvider
from steam_research.offline_fixture import load_fixture
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
from steam_research.projects import ProjectError, initialize_project, project_paths
from steam_research.storage import Database, status_json, status_view

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Collect and analyze Steam competitor research.",
)
crawl_commands = typer.Typer(
    add_completion=False, help="Collect Steam metadata and reviews."
)
export_commands = typer.Typer(
    add_completion=False, help="Export derived research outputs."
)
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
                for diagnostic in run.diagnostics:
                    typer.echo(
                        "  diagnostic\t"
                        f"stage={diagnostic['stage']}\tapp={diagnostic['appid']}\t"
                        f"duration_ms={diagnostic['duration_ms']}\t"
                        f"result_count={diagnostic['result_count']}\t"
                        f"error={diagnostic['error_classification'] or '-'}"
                    )
    except (ProjectError, FileNotFoundError) as error:
        raise typer.BadParameter(str(error)) from error


def _offline_boundaries(
    path: Path | None,
) -> tuple[object | None, object | None, GenerationProvider | None]:
    if path is None:
        return None, None, None
    review_source, store_fetcher, provider = load_fixture(path)
    return review_source, store_fetcher, provider


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
    offline_fixture: Annotated[
        Path | None,
        typer.Option("--offline-fixture", help="Use an explicit local JSON fixture."),
    ] = None,
) -> None:
    """Fetch and persist localized store metadata."""
    context = _context(project)
    try:
        _, store_fetcher, _ = _offline_boundaries(offline_fixture)
        run_id = crawl_store(
            context, _selected_appids(appid, all_apps), fetcher=store_fetcher
        )
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
    offline_fixture: Annotated[
        Path | None,
        typer.Option("--offline-fixture", help="Use an explicit local JSON fixture."),
    ] = None,
) -> None:
    """Crawl resumable positive and negative review streams."""
    context = _context(project)
    try:
        review_source, _, _ = _offline_boundaries(offline_fixture)
        run_id = crawl_reviews_stage(
            context, _selected_appids(appid, all_apps), source=review_source
        )
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
    offline_fixture: Annotated[
        Path | None,
        typer.Option("--offline-fixture", help="Use an explicit local JSON fixture."),
    ] = None,
) -> None:
    """Classify eligible reviews with the configured Stage 1 provider."""
    context = _context(project)
    try:
        _, _, provider = _offline_boundaries(offline_fixture)
        run_id = classify_stage(context, scope=scope, seed=seed, provider=provider)  # type: ignore[arg-type]
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
    offline_fixture: Annotated[
        Path | None,
        typer.Option("--offline-fixture", help="Use an explicit local JSON fixture."),
    ] = None,
) -> None:
    """Generate bounded structured strategy output."""
    context = _context(project)
    try:
        _, _, provider = _offline_boundaries(offline_fixture)
        run_id = synthesize_stage(context, provider=provider)
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
    offline_fixture: Annotated[
        Path | None,
        typer.Option("--offline-fixture", help="Use an explicit local JSON fixture."),
    ] = None,
) -> None:
    """Run collection, classification, aggregation, and synthesis in order."""
    context = _context(project)
    try:
        review_source, store_fetcher, provider = _offline_boundaries(offline_fixture)
        crawl_store(context, fetcher=store_fetcher)
        crawl_reviews_stage(context, source=review_source)
        classify_stage(context, scope=scope, provider=provider)  # type: ignore[arg-type]
        aggregate_stage(context)
        run_id = synthesize_stage(context, provider=provider)
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
    min_words: Annotated[
        int, typer.Option("--min-words", help="Minimum report words.")
    ] = 1500,
    max_words: Annotated[
        int, typer.Option("--max-words", help="Maximum report words.")
    ] = 3000,
) -> None:
    """Render the latest validated synthesis as Markdown."""
    context = _context(project)
    try:
        path = export_report(context, output, min_words=min_words, max_words=max_words)
    except Exception as error:
        typer.echo(f"report export failed: {str(error)[:500]}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"report\tcompleted\t{path}")


if __name__ == "__main__":
    app()
