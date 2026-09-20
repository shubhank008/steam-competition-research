"""Configuration precedence, validation, and manifest tests."""

from pathlib import Path

import pytest

from steam_research.config import ConfigurationError, Secret, load_config


@pytest.mark.parametrize(
    ("env", "cli", "expected"),
    [
        ({}, {}, "project"),
        ({"STEAM_RESEARCH_PROJECT_NAME": "environment"}, {}, "environment"),
        (
            {"STEAM_RESEARCH_PROJECT_NAME": "environment"},
            {"project.name": "cli"},
            "cli",
        ),
    ],
)
def test_precedence_defaults_project_environment_cli(
    tmp_path: Path,
    env: dict[str, str],
    cli: dict[str, str],
    expected: str,
) -> None:
    taxonomy = tmp_path / "taxonomy.yaml"
    taxonomy.write_text("name: test\n", encoding="utf-8")
    project = tmp_path / "project.toml"
    project.write_text(
        f'[project]\nname = "project"\ntaxonomy_path = "{taxonomy}"\n',
        encoding="utf-8",
    )
    config = load_config(project, environ=env, cli_overrides=cli)
    assert config.name == expected


def test_manifest_redacts_secret_and_serializes_paths(tmp_path: Path) -> None:
    taxonomy = tmp_path / "taxonomy.yaml"
    taxonomy.write_text("name: test\n", encoding="utf-8")
    config = load_config(
        None,
        environ={
            "STEAM_RESEARCH_TAXONOMY_PATH": str(taxonomy),
            "OPENAI_API_KEY": "do-not-serialize",
        },
    )
    assert isinstance(config.stage1.api_key, Secret)
    manifest = config.manifest()
    assert "api_key" not in manifest["stage1"]
    assert "do-not-serialize" not in repr(manifest)
    assert manifest["taxonomy_path"] == str(taxonomy)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("STEAM_RESEARCH_STORE_COUNTRY", "USA", "country"),
        ("STEAM_RESEARCH_PAGE_SIZE", "101", "page_size"),
        ("STEAM_RESEARCH_MAX_REVIEWS_PER_APP", "-1", "must not be negative"),
    ],
)
def test_invalid_environment_values_fail(
    tmp_path: Path, key: str, value: str, message: str
) -> None:
    taxonomy = tmp_path / "taxonomy.yaml"
    taxonomy.write_text("name: test\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        load_config(
            None,
            environ={
                "STEAM_RESEARCH_TAXONOMY_PATH": str(taxonomy),
                key: value,
            },
        )


def test_missing_taxonomy_path_fails() -> None:
    with pytest.raises(ConfigurationError, match="taxonomy file does not exist"):
        load_config(
            None, environ={"STEAM_RESEARCH_TAXONOMY_PATH": "/missing/taxonomy.yaml"}
        )
