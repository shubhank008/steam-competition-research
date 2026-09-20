# Technical Specification

## 1. Purpose

This specification translates [PRD.md](PRD.md) into an implementable architecture for a local, CLI-first Steam competitor research pipeline. It defines component boundaries, canonical data, state transitions, schemas, failure behavior, and extension points. Feature branches may refine details through architecture decision records, but must preserve the product requirements or update both documents together.

## 2. System context

The system accepts a research project configuration and Steam app identifiers, communicates with public Steam endpoints and store pages, calls OpenAI-compatible language-model APIs, and writes all canonical state locally.

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

There is no server or GUI in the MVP.

## 3. Architectural principles

1. **Canonical state is queryable.** SQLite is the operational source of truth; no workflow depends on scanning one file per review.
2. **Source fidelity is preserved.** Normalization adds queryable columns without discarding the complete Steam object.
3. **Stages are independently resumable.** Collection, classification, aggregation, and synthesis persist their own inputs, versions, and status.
4. **LLMs extract and reason; code counts.** Stage 1 returns evidence records, deterministic code aggregates them, and Stage 2 interprets prepared evidence.
5. **Outputs are evidence-backed.** Metrics carry denominators and quotes carry source review IDs.
6. **Configuration is versioned.** Structured policy belongs in project files; secrets belong in environment variables.
7. **Adapters stay narrow.** Abstract external boundaries rather than internal business logic.
8. **Documents change with behavior.** Every feature branch updates affected documentation before completion.

## 4. Proposed repository layout

The initial implementation should converge on this layout. A feature branch may adjust names when it records the reason and updates this specification.

```text
.
├── AGENTS.md
├── README.md
├── PRD.md
├── SPEC.md
├── PLAN.md
├── pyproject.toml
├── uv.lock
├── config/
│   ├── default.toml
│   └── taxonomies/
│       ├── universal-core.yaml
│       └── desktop-mascot.yaml
├── src/steam_research/
│   ├── cli/
│   ├── config/
│   ├── domain/
│   ├── storage/
│   ├── collection/
│   │   ├── store_pages/
│   │   └── reviews/
│   ├── classification/
│   ├── aggregation/
│   ├── synthesis/
│   └── reporting/
├── prompts/
│   ├── stage1/
│   └── stage2/
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── contract/
└── data/                  # ignored local runtime data
```

## 5. Technology baseline

These are initial decisions for planning and may be finalized in Phase 1:

- Python 3.12 preferred; minimum supported version to be fixed in `pyproject.toml`.
- `uv` for environment, dependency, and lock-file management.
- Typer for the CLI unless Phase 1 identifies a materially simpler option.
- Pydantic v2 for configuration and external-data validation.
- The standard-library `sqlite3` module with a small ordered migration runner for SQLite access and migrations; this keeps the Phase 1 foundation dependency-free while preserving explicit migration history and parameterized SQL.
- `curl_cffi` as the primary HTTP adapter.
- Patchright as the browser fallback.
- Pandas and/or DuckDB for analytical transforms and Parquet export; aggregation correctness must not depend on a user manually running either tool.
- PyArrow for Parquet serialization.
- Ruff, mypy, and pytest for feedback loops.

Dependency choices are not implemented by this documentation branch. The environment bootstrap branch must pin them and replace provisional commands in README and AGENTS.md with verified commands.

## 6. Configuration model

### 6.1 Sources and precedence

Highest precedence wins:

1. CLI option
2. Environment variable
3. Project TOML/YAML
4. Application default

Secrets are environment-only. The resolved non-secret configuration is serialized into each run manifest.

### 6.2 Illustrative project configuration

```toml
[project]
name = "desktop-mascot-market"
data_dir = "data/desktop-mascot-market"
taxonomy_path = "config/taxonomies/desktop-mascot.yaml"

[steam.store]
country = "US"
language = "english"

[steam.reviews]
language = "all"
purchase_type = "all"
filter = "updated"
include_off_topic = false
page_size = 100
max_reviews_per_app = 0
max_positive_reviews = 0
max_negative_reviews = 0
incremental_overlap_seconds = 86400

[filtering]
large_corpus_threshold = 10000
short_character_threshold = 10
short_word_threshold = 3
high_signal_terms_path = "config/high-signal-terms.yaml"

[classification]
scope = "unclassified-only"
full_corpus_threshold = 10000
batch_review_limit = 20
batch_token_limit = 12000
max_requests = 0
max_input_tokens = 0
max_estimated_cost_usd = 0
random_seed = 42

[classification.progressive]
negative_review_limit = 25000
positive_review_limit = 10000
minimum_per_language = 200

[models.stage1]
provider = "opencode-go"
model = "configured-model-id"
temperature = 0

[models.stage2]
provider = "opencode-go"
model = "configured-model-id"
temperature = 0.2

[report]
minimum_language_sample = 50
target_min_words = 1500
target_max_words = 3000
max_priorities = 7
max_opportunities = 10
max_taglines = 5
```

### 6.3 Environment variables

Names shall use the `STEAM_RESEARCH_` prefix except provider-native secrets. Expected examples:

```text
OPENAI_API_KEY
OPENAI_BASE_URL
STEAM_RESEARCH_PROJECT_CONFIG
STEAM_RESEARCH_TAXONOMY_PATH
STEAM_RESEARCH_STORE_COUNTRY
STEAM_RESEARCH_STORE_LANGUAGE
STEAM_RESEARCH_MAX_REVIEWS_PER_APP
STEAM_RESEARCH_STAGE1_MODEL
STEAM_RESEARCH_STAGE2_MODEL
```

An `.env.example` will document supported values after the configuration models exist. Real `.env` files stay ignored.

## 7. Domain model

### 7.1 Research project

```text
ResearchProject
- id: UUID
- name: string
- database_path: path
- config_path: path
- created_at: timestamp
- updated_at: timestamp
```

### 7.2 Competitor

```text
Competitor
- project_id: UUID
- appid: integer
- store_url: URL
- display_name: optional string
- active: boolean
- added_at: timestamp
```

A project's `(project_id, appid)` pair is unique.


### 7.3 Project storage foundation

The project database is created at `<project-root>/project.sqlite3` and configured on every connection with `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL`, and a bounded busy timeout. `schema_migrations` records applied integer versions; migrations are ordered, transactional, and safe to reapply.

The first migration creates `projects` and `competitors`. A competitor is unique by `(project_id, appid)`, stores a canonical HTTPS Steam URL, and retains UTC ISO-8601 timestamps. Project and competitor services use parameterized SQL and keep path resolution outside the database layer. The current CLI surface is `init PATH`, `app add APPID_OR_STORE_URL --project PATH`, `app list --project PATH`, and `status --project PATH [--json]`. T012 persists sanitized manifests, independent child units, and transactional domain-validated transitions. T013 adds review source lineage and canonical review facts.

### 7.4 Run

Every command that mutates or derives project state creates a run:

```text
Run
- id: UUID
- project_id: UUID
- run_type: store_crawl | review_crawl | classification | aggregation | synthesis | export
- status: pending | running | completed | partial | failed | cancelled
- configuration_json: sanitized JSON
- code_version: git commit or package version
- started_at: timestamp
- completed_at: optional timestamp
- error_summary: optional string
```

A run may have per-app, per-stream, or per-batch child units with independent statuses.

## 8. Adapter contracts

Contracts are behavioral interfaces, not inheritance hierarchies. Python `Protocol` types are preferred where they improve testing.

### 8.1 Store page fetcher

```python
class StorePageFetcher(Protocol):
    def fetch(self, request: StorePageRequest) -> FetchedDocument: ...
```

`StorePageRequest` includes app ID, canonical URL, country, language, timeout, and request metadata. `FetchedDocument` includes status, final URL, headers, body bytes, adapter name, fetch time, and diagnostics.

Initial implementations:

- `CurlCffiStorePageFetcher`
- `PatchrightStorePageFetcher`

Fallback policy must be explicit. Browser fallback may occur for transport denial, challenge/interstitial detection, or a configured required-content check. A parser failure by itself must be surfaced before a fallback masks a markup change.

T025 fallback triggers are limited to HTTP denial/rate-limit outcomes, connection/timeout failures, or recognizable challenge/interstitial markers. A successful HTTP document is passed to the parser without retrying in a browser; parser-field regressions are therefore not fallback triggers. Patchright is optional and its page and context close in a `finally` path on success, navigation failure, and cancellation.

### 8.2 Store page parser

```python
class StorePageParser(Protocol):
    def parse(self, document: FetchedDocument) -> StorePageSnapshot: ...
```

Fetching and parsing remain separate so parser fixtures require no network.

### 8.3 Review source

```python
class ReviewSource(Protocol):
    def fetch_page(self, request: ReviewPageRequest) -> ReviewPage: ...
```

`ReviewPageRequest` includes app ID, review type, encoded cursor, page size, language, purchase type, filter, and off-topic policy. The adapter must use a URL builder that encodes cursor values exactly once. T021 uses `curl_cffi`, passes `json=1`, `num_per_page=100`, `review_type`, `purchase_type`, `filter`, and `filter_offtopic_activity`, then validates JSON before returning the framework-independent page contract. HTTP 429 and 5xx errors are retryable; authentication, not-found, invalid-request, malformed JSON, and schema failures are terminal. The adapter fetches one page only; crawling and persistence remain separate.

### 8.4 LLM provider

```python
class LLMProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResponse: ...
```

Capabilities include native JSON schema, JSON mode, token counting, usage reporting, seed support, maximum context, and retryable error classification. The initial implementation targets an OpenAI-compatible API, but callers must branch on capabilities rather than assume compatibility implies identical behavior.

### 8.5 Exporter

```python
class Exporter(Protocol):
    def export(self, request: ExportRequest) -> ExportResult: ...
```

Initial local exporters are Parquet and Markdown. Notion is out of scope.

Implementation note: T040 uses a provider-independent `StructuredGenerationRequest`/`StructuredGenerationResponse` contract with explicit `ProviderCapabilities`. The OpenCode Go adapter targets `https://opencode.ai/zen/v1/chat/completions`, validates HTTPS/model/key/timeout/retry settings, records token usage, and maps authentication, request, rate-limit, timeout, connection, server, and malformed-response failures without exposing secrets or response bodies. Native JSON Schema is selected only when advertised; otherwise the adapter sends JSON mode. T041 versions the Stage 1 JSON Schema as `stage1-v1` and its prompt as `stage1-prompt-v1`; its validator enforces one-to-one input IDs, taxonomy IDs, enum/range constraints, unverified abandonment, and source-text evidence substrings. No batching, classification scope, or real provider calls are included in these tasks.

## 9. Store metadata contract

Each `StorePageSnapshot` contains:

```text
appid
source_url
country_code
store_language
currency (nullable)
fetched_at
fetch_adapter
parser_schema_version
title
description_short
caption_image_url
screenshots[]: {full_url, thumbnail_url}
total_reviews (nullable)
review_summary (nullable)
release_date: {raw, normalized_date nullable}
developers[]
publishers[]
popular_tags[]
price: {display, initial_minor nullable, final_minor nullable, discount_percent nullable, currency nullable}
description_sections[]: {heading nullable, text, html nullable}
description_full
more_like_this[]: {appid nullable, title nullable, url}
extraction_warnings[]
source_content_hash
```

The implementation should prefer stable structured Steam responses for fields they reliably expose and parse HTML only for missing fields. Field provenance may be recorded when values come from several sources.

Field provenance is a per-contract-field map such as `title -> appdetails` or `title -> html`. The T024 HTTP fetcher requests localized HTML and appdetails JSON independently; the parser accepts both and never performs network I/O. Snapshots retain country and language independently, optional-field warnings, and normalized payload JSON in SQLite.

## 10. Review collection

### 9.1 Collection contract and fixture policy

Steam collection contracts live in `steam_research.steam.contracts` and do not depend on CLI, HTTP, or persistence frameworks. `ReviewPageRequest` fixes `num_per_page=100`, supports both positive and negative streams, and represents Steam purchase and off-topic options explicitly. Cursor values are retained as source values; URL encoding is performed once by the adapter boundary.

Committed fixtures under `tests/unit/fixtures/steam/` are sanitized, minimal JSON responses. They contain no credentials, profile identifiers, or unnecessary personal data. Contract tests validate success, missing fields, invalid schemas, rate limits, and terminal HTTP outcomes without network access. Source hashes use canonical JSON separators and sorted keys, or exact UTF-8 bytes for raw responses. Review text is never included in diagnostics or error messages.

### 10.1 Stream identity

Each review stream is identified by:

```text
(project_id, appid, review_type)
```

where `review_type` is `positive` or `negative`.

Two streams for the same app may run concurrently. Pagination within one stream is sequential.

### 10.2 Initial crawl algorithm

1. Start at cursor `*`.
2. Fetch up to 100 reviews.
3. Persist API-page metadata and response hash.
4. In one database transaction, upsert reviews and update the durable next cursor.
5. Stop on an empty review array, repeated/non-progressing cursor, configured limit, or terminal failure.
6. Mark the stream complete only after a natural or configured successful stop.

### 10.3 Incremental algorithm

For the most recent successfully completed stream crawl, retain its high-water `timestamp_updated`.

1. Start again at cursor `*` with `filter=updated`.
2. Upsert every returned review, including known IDs.
3. Continue through an overlap window below the previous high-water value.
4. Stop when a complete page is older than `previous_high_water - overlap_seconds` and every review on that page already exists with the same `timestamp_updated` and equivalent source hash.
5. Apply all general termination conditions.
6. Advance high-water state only on successful completion.

This prevents a partial prior run, equal timestamps, edits, and polarity changes from making the crawler stop at the first known ID.



### 10.4 Retry policy

Retry connection failures, timeouts, HTTP 429, and retryable 5xx responses using exponential backoff with jitter and server-provided retry hints. Authentication errors, invalid app IDs, invalid response schemas, and exhausted retries are terminal for the current unit and must be recorded.

Defaults must be conservative and configurable. Tests use deterministic injected clock/random behavior.

## 11. SQLite design

SQLite uses WAL mode and foreign keys. Schema changes use migrations. Timestamps use UTC and an unambiguous representation.

### 11.1 Core tables

The first storage feature should implement at least:

- `schema_migrations`
- `projects`
- `competitors`
- `runs`
- `run_units`
- `store_page_snapshots`
- `review_stream_state`
- `review_api_pages`
- `reviews`
- `review_filter_decisions`
- `classification_runs`
- `review_classifications`
- `review_aspects`
- `aggregate_runs`
- `aggregate_metrics`
- `quote_selections`
- `synthesis_runs`

### 11.2 Reviews

Representative SQL shape:

```sql
CREATE TABLE reviews (
    project_id TEXT NOT NULL,
    appid INTEGER NOT NULL,
    recommendationid TEXT NOT NULL,
    language TEXT NOT NULL,
    voted_up INTEGER NOT NULL,
    votes_up INTEGER NOT NULL DEFAULT 0,
    votes_funny INTEGER NOT NULL DEFAULT 0,
    weighted_vote_score REAL,
    comment_count INTEGER NOT NULL DEFAULT 0,
    steam_purchase INTEGER NOT NULL,
    received_for_free INTEGER NOT NULL,
    refunded INTEGER NOT NULL,
    written_during_early_access INTEGER NOT NULL,
    primarily_steam_deck INTEGER,
    playtime_at_review INTEGER,
    playtime_forever INTEGER,
    playtime_last_two_weeks INTEGER,
    last_played INTEGER,
    timestamp_created INTEGER NOT NULL,
    timestamp_updated INTEGER NOT NULL,
    review_text TEXT NOT NULL,
    source_review_type TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_source_run_id TEXT NOT NULL,
    PRIMARY KEY (project_id, appid, recommendationid)
);
```

Author identity fields remain inside `raw_json` unless a concrete query requires normalization. Reports and exports should omit persona name, profile URL, avatar, and Steam ID by default.

Required indexes:

```text
(project_id, appid, language)
(project_id, appid, voted_up, timestamp_updated DESC)
(project_id, appid, playtime_at_review)
(project_id, appid, timestamp_created)
(project_id, appid, source_hash)
```

An FTS5 virtual table for review text is optional at runtime. The application must detect support and report its availability.

### 11.3 API pages

`review_api_pages` stores request parameters, cursor in/out, HTTP outcome, fetch time, review count, response hash, and optional compressed response bytes. Response-body retention is configurable, but metadata and hashes are retained for lineage.

### 11.4 Versioned classifications

A classification is current only when all of these match:

- source review hash
- filtering-policy version
- Stage 1 prompt version/hash
- merged taxonomy version/hash
- provider policy and configured model identifier
- classification schema version

Historical classification rows remain available. A current-view query selects the latest successful compatible result.

## 12. Eligibility and noise filtering

Filtering is deterministic and versioned. It changes LLM eligibility, never source retention.

### 12.1 Always excluded

- Empty or whitespace-only text
- Punctuation/symbol-only text
- Repeated-character or repeated-token spam above configured thresholds
- Detected ASCII art with no meaningful natural-language content

### 12.2 Large-corpus policy

When source review count exceeds the configured large-corpus threshold, reviews below the short-character threshold or short-word threshold become low-information candidates. A candidate remains eligible when it matches language-aware high-signal terms or a configured preservation rule.

Every review receives a decision:

```text
eligible
excluded_empty
excluded_punctuation
excluded_repetition
excluded_ascii_art
excluded_low_information
```

The decision stores policy version and explanatory metadata.
### 12.3 Persistence and policy identity

Eligibility decisions are stored in `review_eligibility` keyed by project, app, recommendation ID, and semantic policy hash. Each row records the source hash, policy version/hash, decision, structured rule reason, and decision timestamp. A policy change marks the prior current row obsolete and inserts a new current row; source `reviews.review_text` and `raw_json` are never rewritten by filtering. The large-corpus boundary is strict (`corpus_size > large_corpus_threshold`), making threshold behavior reproducible.

### 13.3 Loader and merge policy

Taxonomy YAML is loaded with safe parsing and validated before classification. Required category fields are `id`, `name`, and `description`; optional parent and example fields are normalized. Parent IDs must resolve within a document and hierarchy cycles are rejected. Project documents must extend `universal-core`; incompatible duplicate IDs fail, while identical definitions are idempotent. Hashes cover canonical sorted JSON, not source formatting, and category additions remain data-only changes.

## 13. Taxonomy

### 13.1 Universal core

The universal taxonomy defines stable top-level dimensions:

- `technical.stability`
- `technical.performance`
- `compatibility.platform`
- `usability`
- `accessibility`
- `content.features`
- `customization_modding`
- `monetization_value`
- `multiplayer_community`
- `support_updates`
- `use_case`
- `feature_request`

The taxonomy file contains IDs, names, descriptions, and optional positive/negative examples. IDs are stable machine identifiers and must not be reused for changed meanings.

### 13.2 Project extension

A project taxonomy declares `extends: [universal-core]` and adds hierarchical categories. Merge validation rejects duplicate IDs with incompatible definitions. The merged normalized document is hashed and attached to classification runs.

Taxonomy entries are rows referenced by `taxonomy_id`; adding one never adds a database column.

## 14. Stage 1 classification

### 14.1 Input projection

The classifier receives only fields needed to interpret a review:

```json
{
  "input_id": "stable-batch-local-id",
  "review_id": "recommendationid",
  "language": "english",
  "voted_up": false,
  "votes_up": 345,
  "playtime_at_review": 120,
  "review_text": "..."
}
```

The complete object remains in SQLite. Metadata used deterministically after extraction need not be echoed by the model.

### 14.4 Batch execution and scope persistence

T042 projects each eligible review into the bounded Stage 1 fields above and estimates input tokens locally (four UTF-8 characters per estimated token, rounded up). `BatchLimits` enforce both item and estimated-token ceilings. Invalid structured output receives at most the configured repair attempt; persistent invalid batches split recursively until the configured minimum, then write `quarantined` item rows rather than dropping work. Provider failures are retained as explicit item errors. Batch metadata and each item result are written to SQLite with source hash, eligibility policy hash, prompt/schema versions, taxonomy version/hash, model policy, inclusion reason, sampling weight, usage, and error lineage.

Classification runs record request, token, and cost ceilings and usage. A ceiling leaves the run `partial`; completed and quarantined items remain queryable and later runs can retry failed/quarantined or obsolete work. SQLite transactions insert a batch and its pending item lineage atomically, and successful item updates are committed together.

T043 supports `all`, `unclassified-only`, `stratified`, and `progressive` selection. Inputs are sorted by app and recommendation ID before selection. Stratification covers app, language, Steam polarity, playtime bucket, recency, and helpfulness; a SHA-256 seed score makes samples stable without provider dependencies. Selected rows retain source population and sampling weight. Current success requires matching source, eligibility policy, prompt/schema, taxonomy, and model-policy identity; changed identity is eligible again. Gold-set evaluation, aggregation, and reporting are outside T042/T043.


### 14.1 Gold-set evaluation

T044 stores an authored synthetic fixture at `tests/unit/fixtures/gold/stage1_gold_v1.json`; its README is the annotation and provenance authority. The fixture intentionally covers languages, Steam polarity, low/high playtime, short text, eligibility noise, and multiple aspects without identifiers or copied user corpus text. `steam_research.evaluation.load_gold_set` validates fixture shape, and `evaluate` routes candidate output through the production Stage 1 validator before scoring. It reports schema validity (valid batch = 1), coverage and omission rate (expected eligible IDs present), actionability accuracy, micro category precision/recall over taxonomy IDs, exact sentiment and posture agreement, and batch contamination (duplicate, unknown, or cross-batch IDs). Initial pre-tuning regression gates are validity 1.0, omission 0, contamination 0, and 0.80 minimum for the remaining metrics. These gates are not statistical production thresholds; the fixture requires independent annotation and expansion before release decisions. T044 does not implement aggregation or reporting.

### 14.2 Output contract

Each input must produce exactly one object:

```json
{
  "input_id": "stable-batch-local-id",
  "review_id": "123456",
  "is_actionable": true,
  "engagement_posture": "frustrated_but_engaged",
  "overall_sentiment": "negative",
  "sentiment_intensity": 4,
  "aspects": [
    {
      "taxonomy_id": "technical.performance.gpu_usage",
      "sentiment": "negative",
      "intensity": 5,
      "evidence": "uses 30% of my GPU while idle",
      "feature_request": "reduce idle GPU usage",
      "confidence": 0.96
    }
  ],
  "use_cases": ["background_companion", "gaming"],
  "self_reported_abandonment": {
    "mentioned": false,
    "statement_type": null,
    "evidence": null,
    "verified": false
  },
  "summary": "The reviewer likes the concept but reports excessive idle GPU use."
}
```

Allowed engagement postures:

```text
advocacy
satisfied
qualified_satisfaction
mixed
frustrated_but_engaged
rejection
unclear
```

Allowed aspect sentiment values are `positive`, `negative`, `mixed`, and `neutral`. Intensity is 1–5. Confidence is 0–1 and represents classifier confidence, not statistical confidence.

Self-reported abandonment is optional evidence and always `verified=false`. API `refunded` remains a separate source fact.

### 14.3 Batching and validation

- Build batches under both review-count and estimated-token limits.
- Delimit each review as untrusted data.
- Require stable `input_id` echoing and exact one-to-one cardinality.
- Validate JSON syntax, schema, taxonomy IDs, ranges, and evidence-substring presence where practical.
- On invalid output: perform one bounded repair attempt, then split the batch recursively to a configured minimum, then quarantine persistent failures.
- Never silently treat a failed classification as non-actionable.

### 14.4 Scope selection

- `all`: every eligible review needing the selected classifier version.
- `unclassified-only`: new, changed, failed, or obsolete eligible reviews.
- `stratified`: reproducible selection across app, language, polarity, playtime, recency, and helpfulness.
- `progressive`: complete classification under the threshold; above it, apply configured negative and positive ceilings plus stratum minimums, then allow targeted expansion.

Selection records source population, strata, seed, inclusion reason, and sampling weight.

## 15. Deterministic aggregation

Aggregation reads current classifications and source review facts. It writes immutable versioned metrics.

### 15.1 Dimensions

At minimum:

- competitor/app
- review language
- Steam recommendation polarity
- playtime cohort
- review creation and update period
- aspect taxonomy ID
- aspect sentiment and intensity
- engagement posture
- Steam purchase/free/refunded flags
- classification and sampling coverage

### 15.2 Derived retention signals

These are analytical labels, not verified user behavior:

- **early rejection:** negative Steam review, early-friction cohort, and rejection or frustrated posture
- **early dissatisfaction:** actionable negative Steam review in early-friction cohort
- **established dissatisfaction:** actionable negative Steam review in established-use cohort
- **long-use dissatisfaction:** actionable negative Steam review in long-use cohort
- **invested frustration:** long or substantial playtime with frustrated-but-engaged posture
- **qualified retention:** positive Steam review containing material negative aspects
- **strong advocacy:** positive Steam review with advocacy posture and strong positive evidence

Thresholds are configuration values recorded in aggregate manifests.

### 15.3 Rates and weighting

Every rate stores numerator, denominator, population definition, sampling method, and coverage. Sampled estimates use appropriate weights. Helpfulness-based visibility scores are reported separately from unweighted prevalence.

No report may collapse language into geography. Optional broader language clusters must be labeled inferred market clusters and retain their constituent languages.

### 15.4 Representative quotes

Quote selection is deterministic and configurable. It should balance:

- metric/category membership
- classifier confidence
- helpfulness
- recency
- language and competitor diversity
- duplicate/near-duplicate suppression
- confirming and counterexample evidence

Selected quotes retain project ID, app ID, recommendation ID, evidence excerpt, and selection reason. Personal profile fields are not exported.


### 15.5 Implemented T050/T051 contracts

`steam_research.aggregation.aggregate` reads only current eligible reviews and compatible successful Stage 1 rows. It persists immutable `aggregate_runs` and `aggregate_metrics` lineage, with numerator, denominator, population definition, coverage, and caveats. Zero denominators produce `null` values, not zero. Playtime cohorts are `<60` minutes early, `60–599` established, `>=600` long, and missing unknown. Language is retained as language; no geography inference is emitted. API `refunded` is separate from unverified textual abandonment. `select_evidence` bounds source substrings, uses stable confidence-plus-hash ranking, suppresses exact normalized duplicates, limits per language, and retains review/source lineage plus confirming or counterevidence direction in `quote_selections`.

T052 exports five versioned Parquet datasets (`reviews`, `classifications`, `aspects`, `aggregates`, `quotes`) from SQLite using one deterministic file per dataset and bounded row groups controlled by `chunk_size`; `manifest.json` records schema version, selected aggregate run, SQLite schema versions, and lineage hash. PyArrow is an optional `parquet` extra and is imported only at export time. Exports omit raw JSON and reviewer profile identity. T060 defines `stage2-v1` payload/output contracts and `stage2-prompt-v1` provider-independent identity. Payloads contain only metric references and selected bounded evidence, require positive denominators, enforce item limits, support `insufficient_evidence`, and reject geography claims; provider execution is T061.


## 16. Stage 2 synthesis

### 16.1 Inputs

Stage 2 receives:

- project and competitor metadata
- comparable aggregate matrix
- store-positioning comparison
- classification and sampling coverage
- bounded representative evidence
- report schema and item limits

It does not receive the complete raw review corpus.

### 16.2 Structured output

The canonical result contains:

```text
executive_direction: <= 3 findings
competitor_grid: one compact entry per competitor
market_expectations:
  table_stakes: <= configured maximum
  satisfaction_drivers: bounded list
competitor_vulnerabilities: <= configured maximum
recommended_priorities: ranked, <= configured maximum
positioning:
  primary_position
  message_pillars
  tagline_options
  suggested_steam_tags
  visual_merchandising_recommendations
risks_and_counterevidence: <= configured maximum
coverage_and_caveats
```

Each material recommendation contains:

```text
rank
title
action
why_it_matters
affected_competitors
evidence_metrics[]: metric, numerator, denominator, value, population
evidence_review_ids[]
confidence: high | medium | low
effort: high | medium | low | unknown
expected_impact: high | medium | low
validation_action
counterevidence[]
```

The JSON schema enforces item limits. T061 persists a bounded payload, prompt/schema identity, provider usage, output, and lineage in migration 8. Provider output is parsed and post-validated locally; invalid JSON, unknown metric/review references, non-positive denominators, and geography claims receive bounded repair attempts. Request/token/cost ceilings produce resumable partial runs without sending a request that exceeds the token ceiling. T062's deterministic renderer creates Markdown and applies a final word-limit check. An overlong response is rejected, not silently truncated mid-section.

### 16.3 Implemented Stage 2 storage and rendering boundary

`steam_research.synthesis.Synthesizer` accepts only `Stage2EvidencePayload`. Its persisted `payload_json` contains metric references, selected bounded excerpts, and source lineage; it never copies review `raw_json` or a full corpus. `synthesis_runs.output_json` is the canonical validated result. `steam_research.reporting.render_markdown` accepts that result plus the payload, performs no provider calls, emits stable sections and local metric/review/selection IDs, and rejects configured word-limit violations.

## 17. CLI contract

Final command names may be refined during CLI implementation, but required capabilities are:

```text
steam-research init [PATH]
steam-research app add <URL_OR_APPID>
steam-research app list
steam-research crawl store [--app APPID | --all]
steam-research crawl reviews [--app APPID | --all]
steam-research classify [--scope SCOPE] [--app APPID | --all]

steam-research aggregate
steam-research synthesize
steam-research run
steam-research status [--json]
steam-research export parquet
steam-research export report
```

Commands must:

- explain the effective project/configuration;
- avoid interactive prompts unless explicitly requested;
- expose `--help` and machine-readable status;
- use non-zero exit codes for requested work that fails;
- distinguish complete, partial, and failed outcomes;
- handle Ctrl-C by recording interrupted state before exit where possible.

## 18. Observability and cost controls

Structured local logs include run ID, app ID, stage, stream/batch ID, attempt, duration, result count, and error classification. Review text, API secrets, and full model prompts are not logged by default.

LLM usage records request count, input/output tokens when reported, estimated cost, latency, and validation outcome. Zero ceilings mean unlimited by user configuration. A reached ceiling leaves the run partial and resumable.

Crawl metrics include fetched pages, new reviews, updated reviews, unchanged reviews, duplicates, retries, and stop reason.

## 19. Security and privacy

- Treat Steam HTML, JSON, and review text as untrusted input.
- Never execute content obtained from a store page or review.
- Keep secrets in environment variables and redact them from errors.
- Send only the Stage 1 input projection to model providers.
- Document that review text leaves the local environment when a remote provider is configured.
- Exclude author profile identifiers from standard analytical exports and reports.
- Use parameterized SQL exclusively.
- Constrain local paths to explicit project/configuration locations and avoid destructive cleanup outside them.
- Before release, document Steam terms/rate-limit responsibilities and model-provider data policies.

## 20. Testing strategy

### Unit tests

Cover configuration precedence, URL construction/cursor encoding, filtering rules, taxonomy merge/validation, playtime cohorts, schema validation, selection algorithms, rate calculation, and Markdown rendering.

### Integration tests

Use temporary SQLite databases to verify migrations, transactions, idempotent upserts, checkpoint recovery, classification obsolescence, aggregate lineage, and end-to-end pipeline operation with local deterministic provider fixtures.

### Contract tests

Sanitized committed fixtures verify Steam review responses, store page parsing, and OpenAI-compatible response handling. Network-dependent smoke tests must be opt-in and separately marked.

### Evaluation tests

A manually labeled multilingual review set measures:

- schema-valid response rate
- one-output-per-input rate
- actionability classification
- aspect precision/recall by category
- sentiment and posture agreement
- short-review preservation
- cross-review contamination under different batch sizes

Release thresholds must be set before choosing production defaults.

### Failure tests

Inject 429/5xx responses, timeouts, malformed JSON, repeated cursors, partial transactions, invalid taxonomy IDs, omitted batch items, cost-ceiling exhaustion, and Ctrl-C interruption.

## 21. Performance expectations

Initial targets, to be measured and refined:

- Batch SQLite review upserts in transactions rather than committing per row.
- Query common language, polarity, date, and playtime groupings using indexes without full JSON scans.
- Stream review pages and classification candidates; do not hold an entire 100,000-review corpus in memory.
- Keep only one active pagination request per review stream.
- Bound classifier concurrency according to provider limits and database writer behavior.
- Build Parquet outputs in bounded chunks when project size requires it.

## 22. Migration and compatibility policy

- SQLite schema changes require versioned forward migrations.
- Taxonomy IDs are immutable; deprecate rather than repurpose them.
- Prompt, taxonomy, filtering, classification, aggregate, and report schemas each have independent versions.
- A breaking CLI or configuration change requires a migration note in README and relevant plan task.
- Derived artifacts may be rebuilt; source reviews and lineage must survive upgrades.

## 23. Definition of done for a feature branch

A feature branch is complete only when:

1. Its relevant PLAN tasks and PRD acceptance criteria are identified.
2. Production code and tests implement the intended behavior.
3. Offline tests for changed logic pass.
4. Network tests, when relevant, are either run explicitly or documented as not run.
5. README, SPEC, configuration examples, prompts, and AGENTS guidance affected by the change are updated in the same branch.
6. Database migrations and compatibility notes are included when needed.
7. No secret or runtime data is tracked.
8. Commits are regular, coherent checkpoints rather than one monolithic end-of-branch commit.
9. Final branch status and known limitations are recorded for review.

## 24. External references

- Steam user review endpoint: https://partner.steamgames.com/doc/store/getreviews
- Steam language codes: https://partner.steamgames.com/doc/store/localization/languages

## 25. Implemented foundation deviations

T002 uses typed frozen dataclasses and the Python standard library for configuration loading and validation. Pydantic remains the planned dependency for external-data and schema validation in later tasks; introducing it here would add dependency and contract surface before those boundaries exist. T003's domain contracts likewise remain framework-independent.

T003 implements immutable domain dataclasses for app IDs, source SHA-256 hashes, runs, run units, timestamps, and explicit success/failure results. Run and unit transitions are closed over legal lifecycle edges and reject terminal-state mutation. External failures use adapter-neutral codes with retryability derived from code, keeping transport-specific exceptions outside the domain package.

Configuration resolution is implemented as defaults, project TOML, `STEAM_RESEARCH_`/provider environment variables, then dotted CLI overrides. `ApplicationConfig.manifest()` omits provider secrets and non-reproducible loader metadata. The committed `config/default.toml`, universal taxonomy placeholder, and `.env.example` are safe templates only.
