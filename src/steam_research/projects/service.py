"""Project initialization and competitor management services."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from steam_research.storage import Database

_APP_URL = re.compile(
    r"^https?://(?:store\.)?steampowered\.com/app/(?P<appid>[1-9][0-9]*)(?:/[^/?#]*)?/?$",
    re.IGNORECASE,
)


class ProjectError(ValueError):
    """Raised when project or competitor input is invalid."""


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    config: Path
    database: Path


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    paths: ProjectPaths


@dataclass(frozen=True)
class Competitor:
    id: str
    appid: int
    store_url: str
    display_name: str | None


def parse_app_id(value: str) -> int:
    """Parse a positive numeric Steam app ID or canonical store URL."""
    candidate = value.strip()
    if candidate.isdecimal() and int(candidate) > 0:
        return int(candidate)
    match = _APP_URL.fullmatch(candidate)
    if match is None:
        raise ProjectError("expected a positive app ID or Steam store URL")
    return int(match.group("appid"))


def canonical_store_url(appid: int) -> str:
    return f"https://store.steampowered.com/app/{appid}/"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def project_paths(root: Path) -> ProjectPaths:
    resolved = root.expanduser().resolve()
    return ProjectPaths(
        resolved, resolved / "project.toml", resolved / "project.sqlite3"
    )


def initialize_project(root: Path, name: str) -> Project:
    """Create a project directory, config, migrated database, and project row."""
    if not name.strip():
        raise ProjectError("project name must not be empty")
    paths = project_paths(root)
    paths.root.mkdir(parents=True, exist_ok=True)
    if paths.config.exists() or paths.database.exists():
        raise ProjectError(f"project already exists: {paths.root}")
    paths.config.write_text(
        f'[project]\nname = "{name}"\ndata_dir = "{paths.root}"\n', encoding="utf-8"
    )
    project_id = str(uuid4())
    timestamp = _now()
    with Database(paths.database) as database:
        database.migrate()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO projects "
                "(id, name, config_path, database_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    name,
                    str(paths.config),
                    str(paths.database),
                    timestamp,
                    timestamp,
                ),
            )
    return Project(project_id, name, paths)


def _project_id(database: Database) -> str:
    row = database.connection.execute("SELECT id FROM projects LIMIT 1").fetchone()
    if row is None:
        raise ProjectError("project database is not initialized")
    return str(row[0])


def add_competitor(database: Database, value: str) -> tuple[Competitor, bool]:
    """Add an app, returning the existing row and False for duplicates."""
    appid = parse_app_id(value)
    project_id = _project_id(database)
    row = database.connection.execute(
        "SELECT id, appid, store_url, display_name FROM competitors "
        "WHERE project_id = ? AND appid = ?",
        (project_id, appid),
    ).fetchone()
    if row is not None:
        return Competitor(str(row[0]), int(row[1]), str(row[2]), row[3]), False
    competitor = Competitor(str(uuid4()), appid, canonical_store_url(appid), None)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO competitors "
            "(id, project_id, appid, store_url, display_name, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                competitor.id,
                project_id,
                competitor.appid,
                competitor.store_url,
                competitor.display_name,
                _now(),
            ),
        )
    return competitor, True


def list_competitors(database: Database) -> list[Competitor]:
    """Return active competitors ordered by Steam app ID."""
    project_id = _project_id(database)
    rows = database.connection.execute(
        "SELECT id, appid, store_url, display_name FROM competitors "
        "WHERE project_id = ? AND active = 1 ORDER BY appid",
        (project_id,),
    )
    return [Competitor(str(row[0]), int(row[1]), str(row[2]), row[3]) for row in rows]
