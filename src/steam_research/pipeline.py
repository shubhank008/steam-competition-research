"""CLI orchestration over the project services and persisted workflow state."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from steam_research.aggregation import aggregate, select_evidence
from steam_research.classification import (
    BatchLimits,
    Classifier,
    CostCeilings,
    current_classifier_inputs,
)
from steam_research.config import Secret, load_config
from steam_research.domain import AppId, Run, RunStatus, RunUnit, UnitStatus
from steam_research.eligibility import decide_review, persist_eligibility
from steam_research.export import ExportResult, export_parquet
from steam_research.llm import (
    GenerationProvider,
    OpenCodeGoConfig,
    OpenCodeGoProvider,
    ProviderCapabilities,
)
from steam_research.llm.contracts import TransportResponse
from steam_research.projects import Competitor, list_competitors, project_paths
from steam_research.reporting import ReportLimits, render_markdown
from steam_research.stage2 import (
    EvidenceReference,
    MetricReference,
    Stage2EvidencePayload,
    Stage2Limits,
)
from steam_research.steam import (
    CrawlLimits,
    CurlCffiStorePageFetcher,
    SteamReviewApi,
    StoreMetadataParser,
    StorePageRequest,
    crawl_reviews,
)
from steam_research.storage import (
    Database,
    persist_store_snapshot,
)
from steam_research.storage.runs import (
    add_unit,
    create_run,
    transition_run,
    transition_unit,
)
from steam_research.synthesis import Synthesizer, load_synthesis_output
from steam_research.taxonomy import load_merged_taxonomy


class PipelineError(RuntimeError):
    """A safe, user-facing pipeline failure."""


@dataclass(frozen=True)
class ProjectContext:
    root: Path
    database: Path
    config_path: Path
    project_id: str
    config: Any
    competitors: tuple[Competitor, ...]


def open_project(root: Path) -> ProjectContext:
    paths = project_paths(root)
    if not paths.database.is_file() or not paths.config.is_file():
        raise PipelineError(f"project is not initialized: {paths.root}")
    config = load_config(paths.config, validate=False)
    # Project TOML paths are conventionally relative to the project directory.
    config = _resolve_config_paths(config, paths.root)
    config.validate(require_taxonomy=False)
    with Database(paths.database) as database:
        database.migrate()
        row = database.connection.execute("SELECT id FROM projects LIMIT 1").fetchone()
        if row is None:
            raise PipelineError("project database is not initialized")
        project_id = str(row[0])
        competitors = tuple(list_competitors(database))
    return ProjectContext(
        paths.root, paths.database, paths.config, project_id, config, competitors
    )


def _resolve_config_paths(config: Any, root: Path) -> Any:
    from dataclasses import replace

    taxonomy = config.taxonomy_path
    if not taxonomy.is_absolute():
        candidates = (root / taxonomy, Path.cwd() / taxonomy)
        taxonomy = next(
            (candidate for candidate in candidates if candidate.is_file()),
            candidates[0],
        )
    data_dir = (
        config.data_dir if config.data_dir.is_absolute() else root / config.data_dir
    )
    return replace(config, taxonomy_path=taxonomy, data_dir=data_dir)


def _run(
    database: Database, context: ProjectContext, run_type: str, manifest: dict[str, Any]
) -> Run:
    project_id = UUID(context.project_id)
    run = Run.create(project_id, run_type, code_version="0.1.0")
    create_run(database, run, {"project": str(context.root), **manifest})
    transition_run(database, run.id, RunStatus.RUNNING)
    return run


def _finish(
    database: Database, run: Run, status: RunStatus, error: str | None = None
) -> None:
    transition_run(
        database, run.id, status, error_summary=error[:500] if error else None
    )


def _unit(database: Database, run: Run, key: str) -> RunUnit:
    unit = RunUnit.create(run.id, key)
    add_unit(database, unit)
    transition_unit(database, unit.id, UnitStatus.RUNNING)
    return unit


def _done(
    database: Database, unit: RunUnit, status: UnitStatus, error: str | None = None
) -> None:
    transition_unit(
        database, unit.id, status, error_summary=error[:500] if error else None
    )


def _safe_error(error: BaseException) -> str:
    text = str(error).replace("\n", " ")
    for secret in (os.environ.get("OPENAI_API_KEY"), os.environ.get("GH_TOKEN")):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:500] or type(error).__name__


def crawl_store(
    context: ProjectContext,
    appids: Iterable[int] | None = None,
    *,
    fetcher: Any | None = None,
) -> str:
    with Database(context.database) as database:
        run = _run(
            database,
            context,
            "store_crawl",
            {
                "country": context.config.steam.store.country,
                "language": context.config.steam.store.language,
            },
        )
        selected = set(
            appids or (competitor.appid for competitor in context.competitors)
        )
        failures = 0
        fetcher = fetcher or CurlCffiStorePageFetcher()
        parser = StoreMetadataParser()
        for competitor in context.competitors:
            if competitor.appid not in selected:
                continue
            unit = _unit(database, run, str(competitor.appid))
            try:
                request = StorePageRequest(
                    AppId(competitor.appid),
                    context.config.steam.store.country,
                    context.config.steam.store.language,
                )
                document = fetcher.fetch(request)
                structured = fetcher.fetch_structured(request)
                snapshot = parser.parse(document, structured=structured)
                persist_store_snapshot(
                    database, project_id=context.project_id, snapshot=snapshot
                )
            except KeyboardInterrupt:
                _done(database, unit, UnitStatus.CANCELLED, "interrupted")
                _finish(database, run, RunStatus.CANCELLED, "interrupted")
                return str(run.id)
            except Exception as error:
                failures += 1
                _done(database, unit, UnitStatus.FAILED, _safe_error(error))
            else:
                _done(database, unit, UnitStatus.COMPLETED)
        _finish(
            database,
            run,
            RunStatus.PARTIAL if failures else RunStatus.COMPLETED,
            "one or more apps failed" if failures else None,
        )
        return str(run.id)


def crawl_reviews_stage(
    context: ProjectContext, appids: Iterable[int] | None = None, *, source: Any = None
) -> str:
    with Database(context.database) as database:
        limits = context.config.steam.reviews
        run = _run(
            database,
            context,
            "review_crawl",
            {"incremental": True, "limits": limits.__dict__},
        )
        selected = set(
            appids or (competitor.appid for competitor in context.competitors)
        )
        failures = 0
        page_source = source or SteamReviewApi()
        for competitor in context.competitors:
            if competitor.appid not in selected:
                continue
            unit = _unit(database, run, str(competitor.appid))
            try:
                crawl_reviews(
                    database,
                    page_source,
                    project_id=UUID(context.project_id),
                    run_id=run.id,
                    appid=AppId(competitor.appid),
                    language=limits.language,
                    purchase_type=limits.purchase_type,
                    review_filter=limits.filter,
                    limits=CrawlLimits(
                        limits.max_reviews_per_app,
                        limits.max_positive_reviews,
                        limits.max_negative_reviews,
                        limits.max_retries,
                        limits.retry_backoff_seconds,
                        limits.incremental_overlap_seconds,
                    ),
                    incremental=True,
                )
            except KeyboardInterrupt:
                _done(database, unit, UnitStatus.CANCELLED, "interrupted")
                _finish(database, run, RunStatus.CANCELLED, "interrupted")
                return str(run.id)
            except Exception as error:
                failures += 1
                _done(database, unit, UnitStatus.FAILED, _safe_error(error))
            else:
                _done(database, unit, UnitStatus.COMPLETED)
        _finish(
            database,
            run,
            RunStatus.PARTIAL if failures else RunStatus.COMPLETED,
            "one or more apps failed" if failures else None,
        )
        return str(run.id)


def classify_stage(
    context: ProjectContext,
    *,
    scope: Literal[
        "all", "progressive", "stratified", "unclassified-only"
    ] = "unclassified-only",
    seed: int = 0,
    provider: GenerationProvider | None = None,
) -> str:
    with Database(context.database) as database:
        taxonomy = load_merged_taxonomy(
            context.config.taxonomy_path, context.config.taxonomy_path
        )
        reviews = database.connection.execute(
            "SELECT appid, recommendationid, source_hash, review_text "
            "FROM reviews WHERE project_id=? ORDER BY appid, recommendationid",
            (context.project_id,),
        ).fetchall()
        decisions = [
            decide_review(
                project_id=context.project_id,
                appid=int(row[0]),
                recommendationid=str(row[1]),
                source_hash=str(row[2]),
                review_text=str(row[3]),
                corpus_size=len(reviews),
                config=context.config.filtering,
            )
            for row in reviews
        ]
        persist_eligibility(database, decisions)
        inputs = current_classifier_inputs(
            database,
            project_id=context.project_id,
            filtering=context.config.filtering,
            taxonomy_hash=taxonomy.content_hash,
            model_policy=context.config.stage1.model,
            scope=scope,
        )
        provider = provider or _provider(context.config.stage1)
        classifier = Classifier(database, provider)
        return classifier.run(
            project_id=context.project_id,
            inputs=inputs,
            taxonomy=taxonomy,
            model=context.config.stage1.model,
            model_policy=context.config.stage1.model,
            scope=scope,
            seed=seed,
            limits=BatchLimits(),
            ceilings=CostCeilings(),
        )


def aggregate_stage(context: ProjectContext) -> str:
    with Database(context.database) as database:
        return aggregate(database, project_id=context.project_id).run_id


def synthesize_stage(
    context: ProjectContext, *, provider: GenerationProvider | None = None
) -> str:
    with Database(context.database) as database:
        result = database.connection.execute(
            "SELECT id FROM aggregate_runs "
            "WHERE project_id=? AND status='completed' "
            "ORDER BY created_at DESC LIMIT 1",
            (context.project_id,),
        ).fetchone()
        if result is None:
            raise PipelineError("no completed aggregate run; run aggregate first")
        aggregate_id = str(result[0])
        metric_rows = database.connection.execute(
            "SELECT * FROM aggregate_metrics "
            "WHERE run_id=? AND denominator > 0 "
            "ORDER BY metric_id LIMIT 120",
            (aggregate_id,),
        ).fetchall()
        metrics = tuple(
            MetricReference(
                str(row[1]),
                str(row[2]),
                int(row[4]),
                int(row[5]),
                row[6],
                str(row[7]),
                json.loads(row[3]),
            )
            for row in metric_rows
        )
        evidence = tuple(
            EvidenceReference(
                item.selection_id,
                item.recommendationid,
                item.appid,
                item.language,
                item.evidence,
                item.direction,
                item.source_hash,
            )
            for item in select_evidence(database, aggregate_run_id=aggregate_id)
        )
        payload = Stage2EvidencePayload(
            context.project_id,
            aggregate_id,
            metrics,
            evidence,
            tuple(
                {"appid": c.appid, "name": c.display_name or str(c.appid)}
                for c in context.competitors
            ),
            Stage2Limits(),
        )
        return Synthesizer(database, provider or _provider(context.config.stage2)).run(
            payload=payload,
            model=context.config.stage2.model,
            model_policy=context.config.stage2.model,
        )


def export_report(
    context: ProjectContext,
    output: Path,
    *,
    min_words: int = 1500,
    max_words: int = 3000,
) -> Path:
    with Database(context.database) as database:
        output_data, payload_data = load_synthesis_output(
            database, _latest_synthesis(database, context.project_id)
        )
        text = render_markdown(
            output_from_dict(output_data),
            payload_from_dict(payload_data),
            limits=ReportLimits(min_words=min_words, max_words=max_words),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return output


def export_data(context: ProjectContext, output: Path) -> ExportResult:
    with Database(context.database) as database:
        return export_parquet(
            database, project_id=context.project_id, output_dir=output
        )


def _latest_synthesis(database: Database, project_id: str) -> str:
    row = database.connection.execute(
        "SELECT id FROM synthesis_runs "
        "WHERE project_id=? AND status='completed' "
        "ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        raise PipelineError("no completed synthesis run; run synthesize first")
    return str(row[0])


class _HttpJsonTransport:
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> TransportResponse:
        from curl_cffi import requests

        response = requests.post(
            url, headers=dict(headers), json=dict(payload), timeout=timeout
        )
        return TransportResponse(
            response.status_code,
            response.content,
            {
                str(key): str(value)
                for key, value in response.headers.items()
                if value is not None
            },
        )


def _provider(config: Any) -> GenerationProvider:
    key = config.api_key or Secret(os.environ.get("OPENAI_API_KEY", ""))
    if not key.value:
        raise PipelineError(
            "provider credentials are required; configure them through the environment"
        )
    return OpenCodeGoProvider(
        OpenCodeGoConfig(
            config.model,
            key,
            config.base_url or "https://opencode.ai/zen/v1",
            capabilities=ProviderCapabilities(),
        ),
        _HttpJsonTransport(),
    )


def output_from_dict(data: dict[str, Any]) -> Any:
    from steam_research.stage2 import Stage2Output

    return Stage2Output.from_mapping(data)


def payload_from_dict(data: dict[str, Any]) -> Stage2EvidencePayload:
    limits = Stage2Limits(**dict(data.get("limits", {})))
    metrics = tuple(
        MetricReference.from_mapping(item) for item in data.get("metrics", [])
    )
    evidence = tuple(EvidenceReference(**item) for item in data.get("evidence", []))
    return Stage2EvidencePayload(
        str(data["project_id"]),
        str(data["aggregate_run_id"]),
        metrics,
        evidence,
        tuple(dict(item) for item in data.get("competitors", [])),
        limits,
        str(data.get("prompt_version", "stage2-prompt-v1")),
        str(data.get("schema_version", "stage2-v1")),
    )
