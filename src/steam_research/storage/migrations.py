"""Small, dependency-free SQLite migration runner."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

Migration = Callable[[sqlite3.Connection], None]


def _migration_1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL UNIQUE,
            config_path TEXT NOT NULL,
            database_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE competitors (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            appid INTEGER NOT NULL CHECK (appid > 0),
            store_url TEXT NOT NULL,
            display_name TEXT,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            added_at TEXT NOT NULL,
            UNIQUE (project_id, appid)
        );
        CREATE INDEX competitors_project_idx ON competitors(project_id, appid);
        """
    )


def _migration_2(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            run_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN
                ('pending', 'running', 'completed', 'partial', 'failed', 'cancelled')),
            configuration_json TEXT NOT NULL CHECK (json_valid(configuration_json)),
            code_version TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error_summary TEXT
        );
        CREATE TABLE run_units (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            unit_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN
                ('pending', 'running', 'completed', 'failed', 'cancelled')),
            attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
            started_at TEXT,
            completed_at TEXT,
            error_summary TEXT,
            UNIQUE (run_id, unit_key)
        );
        CREATE INDEX runs_project_status_idx ON runs(project_id, status);
        CREATE INDEX run_units_run_status_idx ON run_units(run_id, status);
        """
    )


MIGRATIONS: tuple[Migration, ...] = (_migration_1, _migration_2)


def migrate(connection: sqlite3.Connection) -> None:
    """Apply pending migrations in order, safely on repeated calls."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations")
    }
    try:
        for version, migration in enumerate(MIGRATIONS, start=1):
            if version in applied:
                continue
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (?, datetime('now'))",
                (version,),
            )
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
