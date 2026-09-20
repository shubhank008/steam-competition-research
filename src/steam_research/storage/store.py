"""Persistence for localized Steam store snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from steam_research.steam.store import StorePageSnapshot
from steam_research.storage.database import Database


def persist_store_snapshot(
    database: Database, *, project_id: str, snapshot: StorePageSnapshot
) -> str:
    """Persist one immutable snapshot and its extraction lineage."""
    payload = asdict(snapshot)
    snapshot_id = f"{project_id}:{snapshot.appid.value}:{snapshot.fetched_at}"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO store_page_snapshots "
            "(id, project_id, appid, source_url, country_code, store_language, "
            "currency, fetched_at, fetch_adapter, parser_schema_version, payload_json, "
            "warnings_json, provenance_json, source_content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id,
                project_id,
                snapshot.appid.value,
                snapshot.source_url,
                snapshot.country_code,
                snapshot.store_language,
                snapshot.currency,
                snapshot.fetched_at,
                snapshot.fetch_adapter,
                snapshot.parser_schema_version,
                json.dumps(payload, sort_keys=True, default=str),
                json.dumps(snapshot.extraction_warnings),
                json.dumps(dict(snapshot.field_provenance), sort_keys=True),
                snapshot.source_content_hash,
            ),
        )
    return snapshot_id


def query_store_snapshots(
    database: Database, *, project_id: str, appid: int | None = None
) -> list[dict[str, Any]]:
    """Return stored snapshot metadata ordered by fetch time."""
    if appid is None:
        rows = database.connection.execute(
            "SELECT * FROM store_page_snapshots WHERE project_id = ? "
            "ORDER BY fetched_at, appid",
            (project_id,),
        ).fetchall()
    else:
        rows = database.connection.execute(
            "SELECT * FROM store_page_snapshots WHERE project_id = ? AND appid = ? "
            "ORDER BY fetched_at",
            (project_id, appid),
        ).fetchall()
    return [dict(row) for row in rows]
