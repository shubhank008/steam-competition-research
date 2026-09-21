"""Typed application configuration and sanitized run manifests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Raised when configuration cannot be safely resolved."""


@dataclass(frozen=True)
class Secret:
    """Runtime-only secret that is excluded from serialized manifests."""

    value: str


@dataclass(frozen=True)
class StoreConfig:
    country: str = "US"
    language: str = "english"


@dataclass(frozen=True)
class ReviewConfig:
    language: str = "all"
    purchase_type: str = "all"
    filter: str = "updated"
    page_size: int = 100
    max_reviews_per_app: int = 0
    max_positive_reviews: int = 0
    max_negative_reviews: int = 0
    incremental_overlap_seconds: int = 86400
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class SteamConfig:
    store: StoreConfig = field(default_factory=StoreConfig)
    reviews: ReviewConfig = field(default_factory=ReviewConfig)


@dataclass(frozen=True)
class FilteringConfig:
    policy_version: str = "eligibility-v1"
    large_corpus_threshold: int = 10_000
    short_character_threshold: int = 10
    short_word_threshold: int = 3
    repeated_character_ratio: float = 0.8
    repeated_token_threshold: int = 3
    high_signal_terms: tuple[str, ...] = (
        "bug",
        "crash",
        "error",
        "lag",
        "love",
        "hate",
        "refund",
        "refundé",
        "おすすめ",
        "喜欢",
        "плохо",
        "ошибка",
    )


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "opencode-go"
    model: str = "configured-model-id"
    temperature: float = 0.0
    api_key: Secret | None = field(default=None, repr=False, compare=False)
    base_url: str | None = None


@dataclass(frozen=True)
class ApplicationConfig:
    name: str = "steam-research-project"
    competitors: tuple[int, ...] = ()
    data_dir: Path = Path("data/steam-research-project")
    taxonomy_path: Path = Path("config/taxonomies/universal-core.yaml")
    steam: SteamConfig = field(default_factory=SteamConfig)
    filtering: FilteringConfig = field(default_factory=FilteringConfig)
    stage1: ModelConfig = field(default_factory=ModelConfig)
    stage2: ModelConfig = field(default_factory=lambda: ModelConfig(temperature=0.2))
    project_config_path: Path | None = None

    def validate(self, *, require_taxonomy: bool = True) -> None:
        if any(appid <= 0 for appid in self.competitors):
            raise ConfigurationError(
                "project.competitors must contain positive app IDs"
            )
        country = self.steam.store.country
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            raise ConfigurationError(
                "steam.store.country must be a two-letter country code"
            )
        reviews = self.steam.reviews
        if not 1 <= reviews.page_size <= 100:
            raise ConfigurationError(
                "steam.reviews.page_size must be between 1 and 100"
            )
        for name in (
            "max_reviews_per_app",
            "max_positive_reviews",
            "max_negative_reviews",
            "incremental_overlap_seconds",
            "max_retries",
        ):
            if getattr(reviews, name) < 0:
                raise ConfigurationError(f"steam.reviews.{name} must not be negative")
        filtering = self.filtering
        if not filtering.policy_version.strip():
            raise ConfigurationError("filtering.policy_version must not be empty")
        for name in (
            "large_corpus_threshold",
            "short_character_threshold",
            "short_word_threshold",
            "repeated_token_threshold",
        ):
            if getattr(filtering, name) < 0:
                raise ConfigurationError(f"filtering.{name} must not be negative")
        if not 0 < filtering.repeated_character_ratio <= 1:
            raise ConfigurationError(
                "filtering.repeated_character_ratio must be between 0 and 1"
            )
        if require_taxonomy and not self.taxonomy_path.is_file():
            raise ConfigurationError(
                f"taxonomy file does not exist: {self.taxonomy_path}"
            )

    def manifest(self) -> dict[str, Any]:
        """Return deterministic, non-secret configuration for a run manifest."""
        raw = asdict(self)
        raw.pop("project_config_path", None)
        raw["data_dir"] = str(self.data_dir)
        raw["competitors"] = list(self.competitors)
        raw["taxonomy_path"] = str(self.taxonomy_path)
        for model_name in ("stage1", "stage2"):
            raw[model_name].pop("api_key", None)
        return raw


def _merge_value(target: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _coerce(key: str, value: str) -> Any:
    if key == "project.competitors":
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if key.endswith(
        (
            "page_size",
            "max_reviews_per_app",
            "max_positive_reviews",
            "max_negative_reviews",
            "incremental_overlap_seconds",
            "max_retries",
            "large_corpus_threshold",
            "short_character_threshold",
            "short_word_threshold",
            "repeated_token_threshold",
        )
    ):
        return int(value)
    if key.endswith("temperature"):
        return float(value)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _build(
    data: Mapping[str, Any],
    *,
    project_config_path: Path | None = None,
    validate: bool = True,
) -> ApplicationConfig:
    project = data.get("project", {})
    steam = data.get("steam", {})
    models = data.get("models", {})
    stage1_defaults = asdict(ModelConfig())
    stage2_defaults = asdict(ApplicationConfig().stage2)
    stage1 = {**stage1_defaults, **models.get("stage1", {})}
    stage2 = {**stage2_defaults, **models.get("stage2", {})}
    config = ApplicationConfig(
        name=str(project.get("name", ApplicationConfig.name)),
        competitors=tuple(int(item) for item in project.get("competitors", ())),
        data_dir=Path(project.get("data_dir", ApplicationConfig.data_dir)),
        taxonomy_path=Path(
            project.get("taxonomy_path", ApplicationConfig.taxonomy_path)
        ),
        steam=SteamConfig(
            store=StoreConfig(**{**asdict(StoreConfig()), **steam.get("store", {})}),
            reviews=ReviewConfig(
                **{**asdict(ReviewConfig()), **steam.get("reviews", {})}
            ),
        ),
        filtering=FilteringConfig(
            **{
                **asdict(FilteringConfig()),
                **data.get("filtering", {}),
            }
        ),
        stage1=ModelConfig(**stage1),
        stage2=ModelConfig(**stage2),
        project_config_path=project_config_path,
    )
    if validate:
        config.validate()
    return config
