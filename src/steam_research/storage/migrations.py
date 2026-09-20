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


def _migration_3(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE review_api_pages (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            appid INTEGER NOT NULL CHECK (appid > 0),
            review_type TEXT NOT NULL CHECK (review_type IN ('positive', 'negative')),
            request_json TEXT NOT NULL CHECK (json_valid(request_json)),
            cursor_in TEXT,
            cursor_out TEXT,
            http_status INTEGER,
            fetched_at TEXT NOT NULL,
            review_count INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
            response_hash TEXT,
            response_bytes BLOB
        );
        CREATE TABLE review_stream_state (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            appid INTEGER NOT NULL CHECK (appid > 0),
            review_type TEXT NOT NULL CHECK (review_type IN ('positive', 'negative')),
            cursor TEXT,
            high_water_timestamp INTEGER,
            status TEXT NOT NULL,
            stop_reason TEXT,
            last_page_id TEXT REFERENCES review_api_pages(id),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, appid, review_type)
        );
        CREATE TABLE reviews (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            appid INTEGER NOT NULL CHECK (appid > 0),
            recommendationid TEXT NOT NULL,
            language TEXT NOT NULL,
            voted_up INTEGER NOT NULL CHECK (voted_up IN (0, 1)),
            votes_up INTEGER NOT NULL DEFAULT 0,
            votes_funny INTEGER NOT NULL DEFAULT 0,
            weighted_vote_score REAL,
            comment_count INTEGER NOT NULL DEFAULT 0,
            steam_purchase INTEGER NOT NULL CHECK (steam_purchase IN (0, 1)),
            received_for_free INTEGER NOT NULL CHECK (received_for_free IN (0, 1)),
            refunded INTEGER NOT NULL CHECK (refunded IN (0, 1)),
            written_during_early_access INTEGER NOT NULL
                CHECK (written_during_early_access IN (0, 1)),
            primarily_steam_deck INTEGER CHECK (primarily_steam_deck IN (0, 1)),
            playtime_at_review INTEGER,
            playtime_forever INTEGER,
            playtime_last_two_weeks INTEGER,
            last_played INTEGER,
            timestamp_created INTEGER NOT NULL,
            timestamp_updated INTEGER NOT NULL,
            review_text TEXT NOT NULL,
            source_review_type TEXT NOT NULL
                CHECK (source_review_type IN ('positive', 'negative')),
            source_hash TEXT NOT NULL,
            raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_source_run_id TEXT NOT NULL REFERENCES runs(id),
            PRIMARY KEY (project_id, appid, recommendationid)
        );
        CREATE INDEX reviews_app_language_idx ON reviews(project_id, appid, language);
        CREATE INDEX reviews_app_polarity_updated_idx
            ON reviews(project_id, appid, voted_up, timestamp_updated DESC);
        CREATE INDEX reviews_app_playtime_idx
            ON reviews(project_id, appid, playtime_at_review);
        CREATE INDEX reviews_app_created_idx
            ON reviews(project_id, appid, timestamp_created);
        CREATE INDEX reviews_app_hash_idx ON reviews(project_id, appid, source_hash);
        CREATE INDEX review_pages_app_fetched_idx
            ON review_api_pages(project_id, appid, fetched_at);
        """
    )
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE review_text_fts USING fts5(project_id UNINDEXED, "
            "appid UNINDEXED, recommendationid UNINDEXED, review_text)"
        )
    except sqlite3.OperationalError:
        pass


def _migration_4(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE store_page_snapshots (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            appid INTEGER NOT NULL CHECK (appid > 0),
            source_url TEXT NOT NULL,
            country_code TEXT NOT NULL,
            store_language TEXT NOT NULL,
            currency TEXT,
            fetched_at TEXT NOT NULL,
            fetch_adapter TEXT NOT NULL,
            parser_schema_version TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
            warnings_json TEXT NOT NULL CHECK (json_valid(warnings_json)),
            provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
            source_content_hash TEXT NOT NULL
        );
        CREATE INDEX store_snapshots_app_fetched_idx
            ON store_page_snapshots(project_id, appid, fetched_at);
        CREATE INDEX store_snapshots_hash_idx
            ON store_page_snapshots(project_id, appid, source_content_hash);
        """
    )


MIGRATIONS: tuple[Migration, ...] = (
    _migration_1,
    _migration_2,
    _migration_3,
    _migration_4,
)


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
