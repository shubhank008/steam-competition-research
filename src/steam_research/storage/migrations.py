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


MIGRATIONS: tuple[Migration, ...] = (_migration_1,)


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
    for version, migration in enumerate(MIGRATIONS, start=1):
        if version in applied:
            continue
        migration(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) "
            "VALUES (?, datetime('now'))",
            (version,),
        )
    connection.commit()
