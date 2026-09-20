"""Integration coverage for the SQLite foundation."""

import sqlite3

import pytest

from steam_research.storage import Database


def test_empty_database_migrates_to_current_schema(database: Database) -> None:
    database.migrate()

    tables = {
        row[0]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"schema_migrations", "projects", "competitors"} <= tables
    assert (
        database.connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
        == 3
    )


def test_reapplying_migrations_is_idempotent(database: Database) -> None:
    database.migrate()
    database.migrate()

    assert (
        database.connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
        == 3
    )


def test_foreign_keys_and_wal_are_enabled(database: Database) -> None:
    database.migrate()

    assert database.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert database.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO competitors "
                "(id, project_id, appid, store_url, added_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("c", "missing", 10, "https://store.steampowered.com/app/10", "now"),
            )
