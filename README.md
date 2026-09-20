# Steam Competition Research

Steam Competition Research is a planned CLI pipeline for turning Steam store metadata and large review corpora into a concise, evidence-backed competitive strategy brief. It is designed for product owners and developers who need to understand competitors, find market gaps, prioritize features, avoid technical failures, and improve Steam positioning.

The repository is at the **localized store metadata and resumable review-crawling** stage. T001 provides the package feedback loop; T012/T013 provide run tracking and canonical review storage; T022/T023 add dual-stream crawling, durable page checkpoints, retries, cancellation, incremental overlap, source-hash comparison, and polarity-safe refreshes. See [PLAN.md](PLAN.md) for the implementation sequence.

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
- `curl_cffi` Steam review API adapter with resumable dual-stream crawler and incremental refresh (T021–T023 in progress; offline fixture tests)
- Patchright store-page fallback
- SQLite migrations and repositories
- OpenAI-compatible LLM provider adapter, initially configured for OpenCode Go
- versioned prompts and taxonomy profiles
- Pydantic/JSON-schema validation
- deterministic aggregation and evidence selection
- Parquet and Markdown exporters

## Implemented foundation

T001 provides the package foundation; T012/T013 provide resumable run/unit persistence and canonical review storage; T021 provides the one-page Steam adapter; T022/T023 provide the resumable dual-stream crawler and safe incremental refresh. CLI orchestration remains a future PLAN task.

- Python 3.12 or newer
- `uv` for the environment and locked dependencies
- Typer for the minimal CLI
- pytest, Ruff, and mypy for local feedback

The installed entry point supports project initialization and competitor management:

```text
steam-research init PATH --name project-name
steam-research app add APPID_OR_STORE_URL --project PATH
steam-research app list --project PATH
steam-research --help
steam-research --version
steam-research status --project PATH [--json]
```

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

These commands do not exist yet. T001 creates the package and initial CLI entry point.

## Configuration approach

- Environment variables hold secrets and scalar deployment overrides.
- A versioned project TOML or YAML holds crawl, filtering, classification, aggregation, and report policy.
- A versioned taxonomy YAML or TOML defines project-specific categories.
- CLI flags provide one-run overrides.
- Each stage saves its resolved non-secret configuration for reproducibility.

Expected provider variables include an API key and base URL for the selected OpenAI-compatible service. Copy `.env.example` for local scalar overrides. Secrets are read at runtime and excluded from manifests; project TOML, environment values, and CLI overrides follow the documented precedence.

## Development status

| Area | Status |
|---|---|
| Product requirements | Complete initial version |
| Technical specification | Complete initial version |
| Agentic implementation plan | Complete initial version |
| Python package and CLI | T001 complete |
| Typed configuration and manifests | T002 in progress ([~]) |
| Domain contracts and errors | T003 in progress ([~]) |
| SQLite storage | T010 in progress ([~]) |
| Project and competitor management | T011 in progress ([~]) |
| Run tracking and status | T012 in progress ([~]) |
| Source review storage and query API | T013 in progress ([~]) |
| Steam collection contracts and fixtures | In progress (T020) |
| Stage 1 classification | Not started |
| Aggregation | Not started |
| Stage 2 strategy brief | Not started |

The project is ready to begin **T001: Bootstrap the Python package and feedback loop** from [PLAN.md](PLAN.md).

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
