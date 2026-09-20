import json
from pathlib import Path

from steam_research.domain import AppId
from steam_research.steam import StoreMetadataParser
from steam_research.steam.store import FetchedDocument
from steam_research.storage import (
    Database,
    persist_store_snapshot,
    query_store_snapshots,
)

FIXTURES = Path(__file__).parent / "fixtures" / "steam"


def document() -> FetchedDocument:
    return FetchedDocument(
        AppId(10),
        200,
        "https://store.steampowered.com/app/10/",
        (FIXTURES / "store_success.html").read_bytes(),
        {},
        "curl_cffi",
        "2024-01-01T00:00:00+00:00",
        {"country_code": "DE", "language": "german"},
    )


def test_parser_combines_structured_and_html_fields() -> None:
    structured = json.loads((FIXTURES / "store_details.json").read_text())["10"]["data"]
    snapshot = StoreMetadataParser().parse(document(), structured=structured)
    assert snapshot.country_code == "DE"
    assert snapshot.store_language == "german"
    assert snapshot.title == "Fixture Quest"
    assert snapshot.price.discount_percent == 50
    assert snapshot.release_date.normalized_date == "2024-09-12"
    assert snapshot.platforms == ("windows", "linux")
    assert snapshot.field_provenance["title"] == "appdetails"


def test_parser_warns_for_missing_optional_fields() -> None:
    snapshot = StoreMetadataParser().parse(document(), structured={})
    assert "missing_optional:developers" in snapshot.extraction_warnings
    assert snapshot.title == "Fixture Quest"
    assert snapshot.field_provenance["title"] == "html"


def test_snapshot_persists_payload_warnings_and_provenance(tmp_path: Path) -> None:
    snapshot = StoreMetadataParser().parse(document(), structured={})
    with Database(tmp_path / "project.sqlite3") as database:
        database.migrate()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
                ("p", "P", "c", "d", "now", "now"),
            )
        snapshot_id = persist_store_snapshot(
            database, project_id="p", snapshot=snapshot
        )
        rows = query_store_snapshots(database, project_id="p")
    assert rows[0]["id"] == snapshot_id
    assert json.loads(rows[0]["warnings_json"])
    assert json.loads(rows[0]["provenance_json"])["title"] == "html"
