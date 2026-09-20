# Steam Competition Research

Steam Competition Research is a planned CLI pipeline for turning Steam store metadata and large review corpora into a concise, evidence-backed competitive strategy brief. It is designed for product owners and developers who need to understand competitors, find market gaps, prioritize features, avoid technical failures, and improve Steam positioning.

The repository is at the **release-candidate verification** stage. T001–T073 are implemented on this branch; T072 has documented synthetic scale measurements, and T073 has documented bounded live Steam adapter smoke with provider smoke explicitly skipped. T074 records clean-room verification and release limitations; it is ready for review but is not a production-readiness claim. See [PLAN.md](PLAN.md) for the implementation sequence.

## What the system will do

1. Organize any number of Steam apps or games into a research project.
2. Collect localized store-page metadata using `curl_cffi`, with Patchright as a controlled fallback.
3. Crawl positive and negative Steam review streams concurrently using independent cursors.
4. Preserve complete reviews in queryable SQLite storage and refresh them incrementally.
5. Filter obvious non-actionable input without deleting source evidence.
6. Run a multi-label Stage 1 LLM pass over eligible reviews using a universal plus project-specific taxonomy.
7. Aggregate counts, rates, language-market patterns, playtime cohorts, and representative evidence deterministically.
8. Run a separate Stage 2 model over bounded aggregate evidence.
9. Save validated structured strategy output and render a short Markdown brief containing:
   - competitor strengths and weaknesses;
   - table stakes and unmet demands;
   - early-friction and long-use dissatisfaction;
   - technical landmines;
   - ranked product priorities;
   - positioning, taglines, Steam tags, and store-page recommendations;
   - risks and counterevidence.

## Key decisions

- **SQLite is canonical.** Complete review objects and commonly queried columns live in one project database. The system will not create one file per review.
- **Parquet is derived.** Analytical exports can be rebuilt from SQLite.
- **Language is not geography.** Reports analyze language markets and never claim reviewer location from Steam's language field.
- **Code performs aggregation.** LLMs extract evidence and synthesize strategy; deterministic SQL/Python calculates metrics and denominators.
- **Stage 1 is multi-label.** One review can contribute several technical, feature, usability, monetization, or domain-specific aspects.
- **Taxonomy is configurable.** Projects extend a universal core through versioned YAML or TOML selected by configuration.
- **Outputs stay concise.** The default strategy brief targets 1,500–3,000 words and enforces ranked item limits.
- **The pipeline is incremental.** New, changed, failed, or obsolete classifications can be processed without repeating current work.

## Documentation map

| Document | Purpose |
|---|---|
| [PRD.md](PRD.md) | Product scope, users, requirements, acceptance criteria, success measures, and non-goals |
| [SPEC.md](SPEC.md) | Architecture, storage design, adapter contracts, schemas, state machines, security, and testing strategy |
| [PLAN.md](PLAN.md) | Dependency-ordered, branch-sized implementation tasks with acceptance criteria and suggested commits |
| [AGENTS.md](AGENTS.md) | Required working conventions and guardrails for AI agents and contributors |

When implementation changes behavior, configuration, commands, schemas, or scope, update the affected documents in the same branch.

## Planned architecture

```text
Steam store/API
      |
      v
Collection adapters --> SQLite operational store
                              |
                              v
Eligibility + Stage 1 classification
                              |
                              v
Deterministic aggregation --> Parquet exports
                              |
                              v
Stage 2 structured synthesis --> Markdown strategy brief
```

Planned major components:

- Python CLI
- typed project configuration, explicit precedence, and sanitized run manifests
- framework-independent domain IDs, statuses, transitions, results, timestamps, hashes, and error taxonomy
- `curl_cffi` Steam review API adapter with resumable dual-stream crawler and incremental refresh (T021–T023 implemented; live collection remains opt-in)
- Optional Patchright store-page fallback for explicit denial/challenge responses; install `patchright` and its browser binaries only when browser fallback is needed
- SQLite migrations and repositories
- OpenAI-compatible LLM provider adapter, initially configured for OpenCode Go
- versioned prompts and taxonomy profiles
- Pydantic/JSON-schema validation
- deterministic aggregation and evidence selection
- Parquet and Markdown exporters

## Implemented foundation

T001–T073 provide the package, storage, collection, filtering, taxonomy, classification, aggregation, synthesis, reporting, cohesive CLI pipeline, diagnostics, scale harness, and security-hardening foundations. T074 verifies the release candidate in a clean temporary environment. Provider requests remain opt-in and were not made by the release verification.

- Python 3.12 or newer
- `uv` for the environment and locked dependencies
- Typer for the minimal CLI
- pytest, Ruff, and mypy for local feedback

The installed entry point supports project initialization, competitor management, and the T063 pipeline command surface:

```text
steam-research init PATH --name project-name
steam-research app add APPID_OR_STORE_URL --project PATH
steam-research app list --project PATH
steam-research --help
steam-research --version
steam-research status --project PATH [--json]
steam-research crawl store --project PATH [--app APPID | --all]
steam-research crawl reviews --project PATH [--app APPID | --all]
steam-research classify --project PATH --scope unclassified-only
steam-research aggregate --project PATH
steam-research synthesize --project PATH
steam-research run --project PATH
steam-research export parquet --project PATH --output exports
steam-research export report --project PATH --output strategy-brief.md
steam-research run --project PATH --offline-fixture tests/fixtures/pipeline.json
steam-research export report --project PATH --output brief.md --min-words 1 --max-words 300
```

### Opt-in live Steam smoke

T073 live validation is deliberately separate from the default offline test suite. The bounded smoke uses public app ID `440` (Team Fortress 2), makes one store appdetails request and one review API request for each polarity, requests the adapter maximum page size of 100, performs no retries, and does not persist a project database or response bodies. Run it from the repository root with:

```bash
uv run python - <<'PY'
import json, time
from steam_research.domain import AppId, ExternalError
from steam_research.steam.contracts import ReviewPageRequest, StorePageRequest
from steam_research.steam.reviews import SteamReviewApi
from steam_research.steam.store import CurlCffiStorePageFetcher

app = AppId(440)
out = {"appid": 440, "store": {}, "reviews": []}
start = time.perf_counter()
try:
    data = CurlCffiStorePageFetcher().fetch_structured(
        StorePageRequest(app, country_code="US", language="english", timeout_seconds=10)
    )
    out["store"] = {"success": data is not None, "field_count": len(data or {}),
                    "duration_ms": round((time.perf_counter() - start) * 1000)}
except Exception as exc:
    out["store"] = {"success": False, "error_classification": type(exc).__name__,
                    "duration_ms": round((time.perf_counter() - start) * 1000)}
api = SteamReviewApi()
for polarity in ("positive", "negative"):
    start = time.perf_counter()
    try:
        page = api.fetch_page(ReviewPageRequest(
            app, review_type=polarity, page_size=100, timeout_seconds=10
        ))
        out["reviews"].append({"polarity": polarity, "success": True,
            "count": len(page.reviews), "cursor_present": bool(page.cursor),
            "duration_ms": round((time.perf_counter() - start) * 1000)})
    except ExternalError as exc:
        out["reviews"].append({"polarity": polarity, "success": False,
            "error_classification": exc.code.value,
            "duration_ms": round((time.perf_counter() - start) * 1000)})
print(json.dumps(out, sort_keys=True))
PY
```

The canonical adapter calls are `CurlCffiStorePageFetcher.fetch_structured(StorePageRequest(AppId(440), country_code="US", language="english", timeout_seconds=10))` against `https://store.steampowered.com/api/appdetails`, followed by `SteamReviewApi.fetch_page(ReviewPageRequest(AppId(440), review_type="positive"|"negative", page_size=100, timeout_seconds=10))` against `https://store.steampowered.com/appreviews/440`. Keep any temporary workspace outside the repository (for example, `tempfile.TemporaryDirectory`); never print or save review text, raw responses, profile IDs, cookies, credentials, or generated exports. Stop after the first denial or error and do not use browser fallback.

On 2026-07-08T13:36:31Z in the current container, the store request succeeded in 462 ms and returned 35 structured fields; positive and negative review requests each succeeded with 100 reviews and cursors in 767 ms and 649 ms. No provider request was made: configured credential names were present in the environment, but T073’s bounded provider smoke contract and a safe non-persisting provider fixture are not exposed as a CLI command. These results validate endpoint/adapter compatibility only and are not production-readiness evidence.

`run` executes collection, resumable review crawling, eligibility/classification, deterministic aggregation, and Stage 2 synthesis in order. Stage commands persist run and unit state; partial app or provider failures are visible in `status` and return a non-zero exit code when the requested stage cannot complete. Keyboard interruption records cancelled run/unit state, while durable review checkpoints remain resumable. `--json` serializes the same persisted run/unit view used by the human status command. Store/review collection and configured model providers are live boundaries by default. The explicit `--offline-fixture PATH` option injects a local JSON fixture into store, review, and provider boundaries for deterministic tests; it never changes production defaults or reads credentials. Report word limits are configurable for small fixture reports, with production defaults of 1500–3000 words.

### Diagnostics and security boundaries

`status` and `status --json` include bounded local diagnostics for run ID, app ID, stage, unit or batch ID, attempt, duration, result count, and stable error classification. Diagnostic messages redact URL credentials, bearer values, configured secrets, and credential-looking assignments; they never include review text, prompts, response bodies, or profile identifiers. The explicit offline fixture must be a regular local JSON file no larger than 10 MB. Provider endpoints must use HTTPS without embedded credentials, query strings, or fragments. Review and fixture content is treated as untrusted data inside prompt delimiters and cannot change the required schema or taxonomy. Standard exports omit reviewer profile identity, but review text and selected evidence may still contain personal or sensitive content and should be handled accordingly.

Each project stores its canonical SQLite database at `PATH/project.sqlite3` and its starter configuration at `PATH/project.toml`. SQLite uses WAL mode and enforces foreign keys. Repeating a competitor addition is idempotent and reports `Already present`; invalid app input fails before inserting a row.

## Planned data lifecycle

### Source and operational data

SQLite will retain:

- projects and competitors;
- store-page snapshots and extraction warnings;
- review crawl streams, cursors, page metadata, hashes, and optional compressed responses;
- complete source review JSON plus indexed normalized fields;
- eligibility decisions;
- versioned Stage 1 results and aspect rows;
- deterministic metrics and representative quote selections;
- Stage 2 structured outputs and run lineage.

Runtime project data will live under `data/` by default and will not be committed.
### Provider and Stage 1 contracts

T040 exposes framework-independent structured generation request/response contracts, usage metadata, classified retryable errors, and explicit provider capabilities. The OpenCode Go adapter defaults to `https://opencode.ai/zen/v1`; it uses native JSON Schema only when the capability report allows it and otherwise uses JSON mode. API keys are runtime-only and never appear in manifests or provider errors. T041 defines schema `stage1-v1` and prompt `stage1-prompt-v1`; validation requires exactly one result per input, known taxonomy IDs, supported postures, bounded intensities/confidence, immutable unverified abandonment, and evidence substrings where available. T042/T043 implement `steam_research.classification`: projections are estimated locally, batches obey item/token limits, invalid responses receive bounded repair and recursive split before explicit quarantine, and every item retains SQLite lineage. `all`, `unclassified-only`, `stratified`, and `progressive` scopes use stable seeded selection across app/language/polarity/playtime/recency/helpfulness. Cost ceilings leave resumable runs partial. The example prompt lives at `config/prompts/stage1-v1.txt`.


### Gold-set evaluation

`tests/unit/fixtures/gold/stage1_gold_v1.json` is an authored, synthetic multilingual fixture with positive/negative polarity, low/high playtime, short text, noise, and multi-aspect records. `tests/unit/fixtures/gold/README.md` documents provenance, annotation guidance, and limitations. `steam_research.evaluation` uses the production `validate_results` path and reports schema validity, coverage and omission rate, actionability accuracy, micro category precision/recall, sentiment and engagement-posture agreement, and duplicate/unknown batch contamination. Pre-tuning release gates are schema validity 1.0, omission 0, contamination 0, and at least 0.80 for actionability, category precision/recall, sentiment, and posture; these are initial regression gates, not claims of production readiness.

### Derived artifacts

A completed project can produce:

```text
data/<project>/
├── project.sqlite3
├── manifests/
├── exports/
│   └── parquet/
└── reports/
    ├── strategy.json
    └── strategy.md
```

Paths remain configurable. Structured JSON is canonical for synthesis; Markdown is a deterministic presentation format.


### Deterministic aggregates and evidence

The T050/T051 foundation is available through `steam_research.aggregation`. It computes bounded metrics from current eligible reviews and compatible successful Stage 1 results, preserving counts, denominators, coverage, source/classification lineage, and explicit caveats. Zero-denominator rates are `null`; language labels are never treated as geography; low playtime indicates early friction rather than verified churn; API refunds and unverified abandonment text remain separate. Representative evidence uses stable confidence-plus-hash ranking, exact source-substring quote bounds (oversized evidence is skipped), normalized duplicate suppression, per-language limits, and confirming/counterevidence labels.

T052 adds `steam_research.export.export_parquet`, an optional PyArrow exporter that rebuilds five deterministic datasets (reviews, classifications, aspects, aggregates, and quotes) from SQLite. It writes one file per dataset with bounded row groups, a versioned schema and lineage manifest, and no reviewer profile identity. Install the optional dependency with `uv sync --extra parquet`. T060 adds provider-independent `steam_research.stage2` payload/output contracts with versioned prompt/schema identifiers, metric/evidence lineage, hard limits, explicit insufficient-evidence status, and geography safeguards. T061 adds bounded provider execution in `steam_research.synthesis`, migration-8 persistence, post-validation, repair, and resumable cost ceilings. T062 adds deterministic `steam_research.reporting` Markdown rendering for expectations, vulnerabilities, positioning/store strategy, priorities, risks, caveats, and competitor comparisons; structured JSON remains canonical and rendering never calls a model.

## Planned CLI

The final names may be refined during implementation, but the required command surface is:

```text
steam-research init [PATH]
steam-research app add <URL_OR_APPID>
steam-research app list
steam-research crawl store [--app APPID | --all]
steam-research crawl reviews [--app APPID | --all]
steam-research classify [--scope all|progressive|stratified|unclassified-only]
steam-research aggregate
steam-research synthesize
steam-research run
steam-research status [--json]
steam-research export parquet
steam-research export report
```

The implemented command surface is exercised by the offline CLI regression and the clean-room checks recorded in T074.

## Configuration approach

- Environment variables hold secrets and scalar deployment overrides.
- A versioned project TOML or YAML holds crawl, filtering, classification, aggregation, and report policy.
- A versioned taxonomy YAML or TOML defines project-specific categories.
- CLI flags provide one-run overrides.
- Each stage saves its resolved non-secret configuration for reproducibility.

Expected provider variables include an API key and base URL for the selected OpenAI-compatible service. Copy `.env.example` for local scalar overrides. Secrets are read at runtime and excluded from manifests; project TOML, environment values, and CLI overrides follow the documented precedence.

### Eligibility and taxonomy foundations

Filtering uses `FilteringConfig` defaults from `eligibility-v1`. Empty, punctuation-only, repeated-token/character, and ASCII-art reviews are excluded from Stage 1 input; short reviews are excluded only above the strict large-corpus threshold unless configured multilingual high-signal terms match. Decisions are stored in SQLite with policy identity and obsolete history, while source review text and raw JSON remain unchanged.

Taxonomies are safe-loaded from YAML. Use `config/taxonomies/universal-core.yaml` for the universal profile or `config/taxonomies/desktop-mascot.yaml` as an extension example. Extensions add hierarchical categories under stable IDs and are merged and hashed canonically, so adding a category requires no Python or SQLite schema change. `project.taxonomy_path` and `STEAM_RESEARCH_TAXONOMY_PATH` select the project taxonomy.
## Development status

| Area | Status |
|---|---|
| Product requirements | Complete initial version |
| Technical specification | Complete initial version |
| Agentic implementation plan | Complete initial version |
| Python package and CLI | T001 complete |
| Typed configuration and manifests | Implemented (T002) |
| Domain contracts and errors | Implemented (T003) |
| SQLite storage and project management | Implemented (T010–T013) |
| Steam collection contracts and fixtures | Implemented (T020–T025) |
| Eligibility and taxonomy | Implemented (T030–T031) |
| Stage 1 classification and evaluation | Implemented (T040–T044) |
| Aggregation and Parquet export | Implemented (T050–T052) |
| Stage 2 strategy brief | Implemented (T060–T062) |
| Cohesive CLI pipeline | Implemented (T063) |
| Diagnostics and security hardening | Implemented (T070–T071) |
| Scale benchmark harness | Implemented and measured (T072) |
| Live Steam/provider smoke | Steam adapter smoke documented; provider smoke skipped (T073) |
| Release-candidate verification | In progress ([~] T074) |

T072 adds an offline scale harness at `tests/benchmarks/scale_benchmark.py`. Repeat seeded 100k/1M measurements with `uv run python tests/benchmarks/scale_benchmark.py --size 100000 --size 1000000 --seed-fraction 0.01 --no-export`; include the optional writer on 100k with `uv run python tests/benchmarks/scale_benchmark.py --size 100000 --seed-fraction 0.01`. `--seed-fraction` creates a deterministic, bounded analytical subset (maximum 100k rows) with eligibility decisions and successful Stage 1 lineage/aspects; it does not call a provider. The harness generates data in code, uses temporary SQLite/Parquet paths, requires no network or credentials, and reports JSON database size, ingest, query, aggregate, evidence, classification planning, export rows/time, and peak-RSS measurements. On 2026-07-08 in the current Linux x86_64 container (6 CPUs, Python 3.13.14, uv 0.12.0), the 100k seeded run used 86,786,048 bytes, ingested in 13.204s, aggregated 1,000 rows in 0.040s, selected 9 evidence rows in 0.025s, and exported 100,000 reviews plus 1,000 classifications and aspects in 1.798s at 114.9 MiB peak RSS. The 1M no-export run used 902,270,976 bytes, ingested in 213.022s, aggregated 10,000 rows in 0.406s, selected 9 evidence rows in 0.246s, and held 88.5 MiB peak RSS. 1M export was omitted because ingest already takes several minutes; 100k covers the bounded Parquet writer path. Live smoke and provider execution remain out of scope.

The project is ready for review of **T074: Prepare MVP release candidate** from [PLAN.md](PLAN.md). T074 verification covers clean-room installation, offline behavior, packaging, and artifact hygiene; it does not establish production readiness or validate provider behavior.

## Contribution workflow

Two rules are mandatory:

1. **Documentation stays current.** Every feature or material behavior change updates affected documentation, examples, prompts, and configuration in the same branch. A feature is incomplete while implementation and documentation disagree.
2. **Major work gets its own branch and regular commits.** Start each major feature or material change from an up-to-date default branch. Commit coherent working checkpoints throughout development; do not finish a long edit with one monolithic commit.

Recommended workflow:

```bash
git switch main
git pull --ff-only
git switch -c feat/descriptive-feature

# Implement a coherent slice, test it, then commit it.
git add <focused-files>
git commit -m "feat: add coherent capability"

# Continue with the next working slice and its documentation/tests.
```

Do not push directly to `main`. Do not commit `.env`, runtime databases, raw reviews, model responses containing private credentials, browser state, or generated data directories.

## Setup and validation

Install the locked runtime and development dependencies with `uv`:

```bash
uv sync --locked
```

Run the package and feedback loop commands from the repository root:

```bash
uv run steam-research --help
uv run pytest
uv run pytest tests/test_cli.py
uv run ruff format --check .
uv run ruff check .
uv run mypy
```

The smoke test invokes the installed `steam-research` console script through a real subprocess. The default test suite is offline and does not require Steam or model-provider credentials.

## Data and research caveats

- Steam review language describes the language of a review, not the reviewer's country.
- Low playtime plus negative sentiment is an early-friction proxy, not verified churn.
- Text such as “I refunded” is self-reported and unverified; Steam's `refunded` field is retained separately as an API fact.
- Review ordering can mutate during a crawl, so incremental collection uses overlap and high-water safeguards rather than stopping at the first known review.
- Store prices and metadata depend on country, language, currency, and fetch time.
- Remote LLM use sends the configured Stage 1 text projection to the selected provider; provider data policies must be reviewed before production use.

## External references

- [Steam user review endpoint](https://partner.steamgames.com/doc/store/getreviews)
- [Steam-supported language codes](https://partner.steamgames.com/doc/store/localization/languages)
