"""Configuration loading with explicit source precedence."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from steam_research.config.models import (
    ApplicationConfig,
    Secret,
    _build,
    _coerce,
    _merge_value,
)

ENV_KEYS = {
    "STEAM_RESEARCH_PROJECT_NAME": "project.name",
    "STEAM_RESEARCH_COMPETITORS": "project.competitors",
    "STEAM_RESEARCH_DATA_DIR": "project.data_dir",
    "STEAM_RESEARCH_TAXONOMY_PATH": "project.taxonomy_path",
    "STEAM_RESEARCH_STORE_COUNTRY": "steam.store.country",
    "STEAM_RESEARCH_STORE_LANGUAGE": "steam.store.language",
    "STEAM_RESEARCH_PAGE_SIZE": "steam.reviews.page_size",
    "STEAM_RESEARCH_MAX_REVIEWS_PER_APP": "steam.reviews.max_reviews_per_app",
    "STEAM_RESEARCH_MAX_POSITIVE_REVIEWS": "steam.reviews.max_positive_reviews",
    "STEAM_RESEARCH_MAX_NEGATIVE_REVIEWS": "steam.reviews.max_negative_reviews",
    "STEAM_RESEARCH_MAX_RETRIES": "steam.reviews.max_retries",
    "STEAM_RESEARCH_FILTER_POLICY_VERSION": "filtering.policy_version",
    "STEAM_RESEARCH_LARGE_CORPUS_THRESHOLD": "filtering.large_corpus_threshold",
    "STEAM_RESEARCH_SHORT_CHARACTER_THRESHOLD": "filtering.short_character_threshold",
    "STEAM_RESEARCH_SHORT_WORD_THRESHOLD": "filtering.short_word_threshold",
    "STEAM_RESEARCH_STAGE1_MODEL": "models.stage1.model",
    "STEAM_RESEARCH_STAGE2_MODEL": "models.stage2.model",
    "OPENAI_API_KEY": "models.stage1.api_key",
    "OPENAI_BASE_URL": "models.stage1.base_url",
}


def _dotenv_environment(project_path: Path | None) -> dict[str, str]:
    candidates = []
    if project_path is not None:
        candidates.append(project_path.parent / ".env")
    candidates.append(Path.cwd() / ".env")
    for candidate in candidates:
        if candidate.is_file():
            return {
                key: value
                for key, value in dotenv_values(candidate).items()
                if value is not None
            }
    return {}


def load_config(
    project_path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    validate: bool = True,
) -> ApplicationConfig:
    """Resolve defaults, TOML, environment, then CLI overrides."""
    data: dict[str, Any] = {}
    if project_path is not None:
        with project_path.open("rb") as stream:
            data = tomllib.load(stream)
    process_env = dict(os.environ if environ is None else environ)
    dotenv_env = {} if environ is not None else _dotenv_environment(project_path)
    env = {**dotenv_env, **process_env}
    for env_name, dotted_key in ENV_KEYS.items():
        if env_name in env:
            _merge_value(data, dotted_key, _coerce(dotted_key, env[env_name]))
    for dotted_key, value in (cli_overrides or {}).items():
        _merge_value(data, dotted_key, value)
    for name in ("stage1", "stage2"):
        model = data.get("models", {}).get(name, {})
        if "api_key" in model and not isinstance(model["api_key"], Secret):
            model["api_key"] = Secret(str(model["api_key"]))
    return _build(data, project_config_path=project_path, validate=validate)
