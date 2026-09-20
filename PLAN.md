# Agentic Implementation Plan

## 1. How to use this plan

This is the execution map for [PRD.md](PRD.md) and [SPEC.md](SPEC.md). Each numbered task is intended to be small enough for one focused agent session and independently reviewable. A task may be split further when implementation reveals risk, but unrelated tasks must not be combined merely to reduce branch count.

### Mandatory delivery rules

1. **One major feature or material change per branch.** Start from an up-to-date default branch. Use the suggested branch name or an equivalent descriptive name.
2. **Commit at coherent checkpoints.** Make regular commits after a working slice, tests, and documentation updates. Do not perform a long implementation and finish with one monolithic commit.
3. **Documentation ships with behavior.** Update all affected requirements, specification, README usage, configuration examples, prompts, and agent instructions in the same branch.
4. **Tests use real code paths.** Prefer local fixtures and temporary databases. Mock only an external boundary that cannot be exercised deterministically, and document why.
5. **Do not start blocked work.** A task may begin only when its listed dependencies are complete or explicitly waived with a documented replacement.
6. **Leave a clean handoff.** Before review, update this plan's checkbox/status, record deviations, run the branch validation commands, and summarize unverified behavior.

### Task states

- `[ ]` not started
- `[~]` in progress on a feature branch
- `[x]` complete and merged
- `[!]` blocked; add the reason directly below the task

Do not mark a task complete merely because code exists. All acceptance criteria and the repository definition of done must pass.

## 2. Delivery sequence

```text
Phase 0: foundation
   |
Phase 1: project + storage
   |
Phase 2: collection --------------------+
   |                                    |
Phase 3: filtering + taxonomy           |
   |                                    |
Phase 4: Stage 1 classification <-------+
   |
Phase 5: aggregation
   |
Phase 6: Stage 2 synthesis + report
   |
Phase 7: hardening and release
```

Tasks within a phase may run in parallel only when they do not edit the same contracts or migration sequence.

## 3. Phase 0 — Engineering foundation

### [~] T001: Bootstrap the Python package and feedback loop

- **Suggested branch:** `chore/python-bootstrap`
- **Depends on:** none
- **PRD:** NFR-009, NFR-010
- **Scope:** Create `pyproject.toml`, package layout, locked dependencies, pytest configuration, Ruff, mypy, and a minimal CLI entry point.
- **Deliverables:**
  - `src/steam_research/` importable package
  - `steam-research --help`
  - locked runtime and development dependencies
  - documented setup/test/lint/typecheck commands
  - one smoke test through the installed CLI entry point
- **Acceptance:**
  - Fresh documented setup succeeds.
  - Test, lint, format-check, and typecheck commands pass.
  - README and AGENTS commands are replaced with verified commands rather than provisional text.
- **Suggested commits:** package skeleton; tool configuration; CLI smoke test and docs.

### [~] T002: Implement typed configuration and run manifests

- **Suggested branch:** `feat/project-configuration`
- **Depends on:** T001
- **PRD:** US-001, FR-001–FR-004
- **Scope:** Define typed application/project configuration, precedence, secret handling, validation, and sanitized resolved manifests.
- **Deliverables:** default configuration; loader; environment overrides; CLI overrides; manifest serializer; `.env.example`.
- **Acceptance:**
  - Precedence is covered by table-driven tests.
  - Secrets never appear in serialized manifests or test snapshots.
  - Invalid country, page size, negative limits, and missing taxonomy paths fail clearly.
- **Suggested commits:** config models; precedence and redaction; examples/docs.

### [~] T003: Establish domain models and error taxonomy

- **Suggested branch:** `feat/domain-contracts`
- **Depends on:** T001
- **PRD:** US-013, NFR-002, NFR-003
- **Scope:** Implement shared IDs, statuses, result types, timestamps, source hashes, and retryable/terminal error categories.
- **Acceptance:**
  - Run and run-unit state transitions reject illegal transitions.
  - External errors can be classified without importing adapter implementations into the domain layer.
  - SPEC deviations are documented.
- **Suggested commits:** domain value objects; state transitions; errors/tests/docs.

## 4. Phase 1 — Projects and canonical storage

### [~] T010: Add SQLite engine and migration framework

- **Suggested branch:** `feat/sqlite-foundation`
- **Depends on:** T001, T003
- **PRD:** FR-020–FR-024, NFR-002, NFR-004
- **Scope:** Initialize SQLite with WAL/foreign keys, migration tooling, transaction helpers, and temporary-database test fixtures.
- **Acceptance:**
  - An empty database migrates to current schema.
  - Reapplying migrations is safe.
  - Foreign keys and WAL settings are verified in integration tests.
- **Suggested commits:** engine/transactions; migrations; integration fixtures/docs.

### [ ] T011: Implement project and competitor management

- **Suggested branch:** `feat/project-management`
- **Depends on:** T002, T010
- **PRD:** US-001, US-002, FR-001
- **Scope:** Implement `init`, app ID/store URL parsing, competitor add/list, duplicates, and project path resolution.
- **Acceptance:**
  - Valid URLs and numeric IDs create one competitor.
  - Invalid input changes no state.
  - Duplicate additions are idempotent and clearly reported.
  - CLI integration tests use a temporary project.
- **Suggested commits:** repositories; app parsing; CLI/tests/docs.

### [ ] T012: Implement runs, units, and status reporting

- **Suggested branch:** `feat/run-tracking`
- **Depends on:** T002, T003, T010
- **PRD:** US-013, FR-051, FR-052
- **Scope:** Persist command runs and child work units, partial/failure states, sanitized manifests, and human/JSON status views.
- **Acceptance:**
  - Interrupted or failed units remain distinguishable from completed work.
  - JSON status is schema-tested.
  - Illegal state transitions fail transactionally.
- **Suggested commits:** schema/repositories; status service; CLI/tests/docs.

### [ ] T013: Implement source review storage and query API

- **Suggested branch:** `feat/review-storage`
- **Depends on:** T010, T012
- **PRD:** US-006, FR-020–FR-023
- **Scope:** Add review, API-page, and stream-state tables; typed normalized columns; raw JSON; indexes; idempotent upserts; optional FTS detection.
- **Acceptance:**
  - A complete Steam fixture round-trips through `raw_json` without field loss.
  - New, changed, and unchanged upserts are counted correctly.
  - Polarity changes update one canonical review.
  - Query tests cover app, language, polarity, date, and playtime.
  - A 100,000-row generated fixture exercises batch insertion within a recorded baseline.
- **Suggested commits:** migrations/models; upsert/query service; scale/FTS tests and docs.

## 5. Phase 2 — Steam collection

### [ ] T020: Define collection contracts and sanitized fixtures

- **Suggested branch:** `feat/collection-contracts`
- **Depends on:** T003
- **PRD:** FR-010, NFR-009
- **Scope:** Implement store/review request and response contracts, fixture conventions, hashing, and adapter error mapping.
- **Acceptance:**
  - Fixtures represent success, missing fields, invalid schema, rate limiting, and terminal errors.
  - Contracts contain no persistence or CLI dependencies.
- **Suggested commits:** contracts; fixtures; contract tests/docs.

### [ ] T021: Implement the Steam review API adapter

- **Suggested branch:** `feat/steam-review-adapter`
- **Depends on:** T001, T020
- **PRD:** US-004, FR-013–FR-016
- **Scope:** Use `curl_cffi` to request review pages, construct parameters, encode cursors exactly once, validate responses, and classify errors.
- **Acceptance:**
  - Tests cover special-character cursors, page size 100, both review types, purchase/off-topic options, malformed responses, 429, and 5xx.
  - Tests use committed responses; live smoke testing is opt-in.
  - The official Steam review API reference remains linked in docs.
- **Suggested commits:** URL/request builder; response parser; failure tests/docs.

### [ ] T022: Implement resumable dual-stream crawling

- **Suggested branch:** `feat/review-crawler`
- **Depends on:** T012, T013, T021
- **PRD:** US-004, US-005
- **Scope:** Orchestrate concurrent positive/negative streams, sequential cursor paging, transaction checkpoints, retry/backoff, limits, stop reasons, and cancellation.
- **Acceptance:**
  - Positive and negative fixtures progress independently.
  - Crash-after-page injection resumes at the durable cursor.
  - Repeated/no-progress cursors terminate explicitly.
  - Limits have deterministic semantics under concurrency.
  - Ctrl-C leaves resumable partial status.
- **Suggested commits:** single-stream state machine; dual-stream coordinator; interruption/limit tests and docs.

### [ ] T023: Implement safe incremental review refresh

- **Suggested branch:** `feat/incremental-review-refresh`
- **Depends on:** T022
- **PRD:** US-005
- **Scope:** Add high-water tracking, overlap stopping, source-hash comparison, and polarity-change handling.
- **Acceptance:**
  - Encountering one known review does not stop the crawl.
  - A fully known page inside the overlap can stop according to SPEC.
  - Edited reviews update and become eligible for reclassification.
  - High-water state does not advance after partial failure.
- **Suggested commits:** high-water state; overlap algorithm; mutation/failure tests/docs.

### [ ] T024: Implement store metadata parsing with `curl_cffi`

- **Suggested branch:** `feat/store-page-http`
- **Depends on:** T010, T011, T020
- **PRD:** US-003, FR-010–FR-012
- **Scope:** Fetch localized store content, combine structured endpoints and HTML parsing, persist snapshots, warnings, and provenance.
- **Acceptance:**
  - All PRD store fields are extracted when present in fixtures.
  - Country and language are independently applied and persisted.
  - Missing optional fields yield warnings.
  - Parser tests run without network.
- **Suggested commits:** structured metadata source; HTML parser; storage/CLI/docs.

### [ ] T025: Add Patchright store fallback

- **Suggested branch:** `feat/store-page-browser-fallback`
- **Depends on:** T024
- **PRD:** US-003, FR-011
- **Scope:** Implement browser fetching and explicit fallback triggers without masking parser regressions.
- **Acceptance:**
  - Transport denial/challenge fixtures trigger fallback.
  - A parser-field regression alone does not silently trigger fallback.
  - Browser resources close on success, failure, and cancellation.
  - Optional dependency/setup requirements are documented.
- **Suggested commits:** browser adapter; fallback policy; lifecycle tests/docs.

## 6. Phase 3 — Eligibility and taxonomy

### [ ] T030: Implement versioned noise and eligibility filtering

- **Suggested branch:** `feat/review-eligibility`
- **Depends on:** T002, T013
- **PRD:** US-007
- **Scope:** Detect empty, punctuation, repetition, ASCII art, and conditional low-information candidates; preserve high-signal short reviews; store decisions.
- **Acceptance:**
  - Multilingual and short high-signal fixtures are retained.
  - Filtering never deletes or modifies source review text.
  - Policy changes make prior decisions obsolete.
  - Corpus threshold behavior is deterministic.
- **Suggested commits:** core filters; large-corpus/high-signal policy; persistence/tests/docs.

### [ ] T031: Implement universal and project taxonomy loading

- **Suggested branch:** `feat/configurable-taxonomy`
- **Depends on:** T002
- **PRD:** US-008, FR-032, FR-033
- **Scope:** Ship universal core and desktop-mascot example, merge extensions, validate hierarchy/IDs, and hash normalized definitions.
- **Acceptance:**
  - A project selects taxonomy using project config or environment override.
  - Duplicate incompatible IDs and missing fields fail before classification.
  - Adding a category requires no code or database migration.
  - Taxonomy hash is stable across non-semantic formatting changes.
- **Suggested commits:** schema/normalization; universal taxonomy; extension example/tests/docs.

## 7. Phase 4 — Stage 1 classification

### [ ] T040: Implement provider contracts and OpenAI-compatible adapter

- **Suggested branch:** `feat/llm-provider-adapter`
- **Depends on:** T001, T003
- **PRD:** FR-030, FR-031
- **Scope:** Add structured generation requests, capability reporting, usage data, retry/error mapping, and OpenAI-compatible transport configured for OpenCode Go.
- **Acceptance:**
  - Provider-native schema mode and fallback JSON mode are capability-gated.
  - Secrets are absent from logs and errors.
  - Contract tests cover valid, invalid, rate-limited, and truncated responses.
  - A local deterministic fake implements the same contract for pipeline tests.
- **Suggested commits:** contracts/fake; compatible transport; capabilities/errors/docs.

### [ ] T041: Define Stage 1 schema and prompt package

- **Suggested branch:** `feat/stage1-schema-prompt`
- **Depends on:** T031, T040
- **PRD:** US-009, FR-034
- **Scope:** Create versioned Pydantic/JSON schema, system prompt template, taxonomy injection, untrusted-data delimiters, and output validators.
- **Acceptance:**
  - One result is required per stable input ID.
  - Multiple aspects per review validate.
  - Unknown taxonomy IDs, invalid ranges, duplicate/missing input IDs, and unsupported postures fail.
  - Evidence spans are checked against source text where practical.
- **Suggested commits:** output models; prompt builder; adversarial/schema tests/docs.

### [ ] T042: Implement token-aware Stage 1 batching and recovery

- **Suggested branch:** `feat/stage1-batching`
- **Depends on:** T030, T041
- **PRD:** US-009
- **Scope:** Select eligible inputs, project fields, estimate token use, batch under dual limits, perform bounded repair/split/quarantine, and persist results.
- **Acceptance:**
  - No eligible input silently disappears.
  - Oversized individual reviews follow documented handling.
  - Repair occurs at most as configured; splitting terminates.
  - Persistent failures remain queryable and retryable.
  - Successful batches commit atomically.
- **Suggested commits:** input/batch planner; execution/recovery; persistence/failure tests/docs.

### [ ] T043: Implement classification scope selection

- **Suggested branch:** `feat/classification-scopes`
- **Depends on:** T013, T030, T042
- **PRD:** US-010, FR-035
- **Scope:** Implement `all`, `unclassified-only`, `stratified`, and `progressive`, including source populations, seeds, weights, ceilings, and obsolescence.
- **Acceptance:**
  - A fixed project and seed produce identical selections.
  - Selected strata include polarity, language, playtime, recency, and helpfulness.
  - Changed source/prompt/taxonomy/model policy becomes eligible again.
  - Cost/request/token ceilings leave resumable partial status.
- **Suggested commits:** obsolescence/current view; stratified selector; progressive policy and ceilings; CLI/docs.

### [ ] T044: Build and evaluate a multilingual gold set

- **Suggested branch:** `test/stage1-evaluation`
- **Depends on:** T041, T042
- **PRD:** Success metrics, NFR-006
- **Scope:** Add a legally suitable manually labeled corpus spanning languages, polarity, playtime, short text, noise, and multi-aspect feedback; implement evaluation metrics.
- **Acceptance:**
  - Dataset provenance and labeling guidance are documented.
  - Evaluation reports schema validity, omission, actionability, category precision/recall, sentiment/posture agreement, and batch contamination.
  - Release thresholds are recorded before production model tuning.
  - Personal profile metadata is not present.
- **Suggested commits:** labeling schema/guidance; gold fixtures; evaluator and baseline report.

## 8. Phase 5 — Aggregation and analytical exports

### [ ] T050: Implement deterministic aggregate engine

- **Suggested branch:** `feat/aggregate-engine`
- **Depends on:** T013, T031, T043
- **PRD:** US-011, FR-040–FR-043
- **Scope:** Compute comparable metrics, cohorts, derived retention signals, coverage, denominators, periods, prevalence, and visibility impact.
- **Acceptance:**
  - Every rate stores numerator, denominator, and population definition.
  - Sampled and full-corpus fixtures produce expected values.
  - Language-market labels never imply geography.
  - API refund and unverified textual claims remain distinct.
  - Aggregate runs are reproducible from recorded inputs.
- **Suggested commits:** cohort/metric primitives; cross-competitor aggregation; sampling/coverage tests/docs.

### [ ] T051: Implement representative evidence selection

- **Suggested branch:** `feat/evidence-selection`
- **Depends on:** T050
- **PRD:** US-011
- **Scope:** Select bounded, diverse, traceable quotes with duplicate suppression and counterevidence.
- **Acceptance:**
  - Same inputs/configuration produce the same quote set.
  - Each quote resolves to a review and aggregate/category.
  - No persona, profile URL, avatar, or Steam ID is exported.
  - Confirming and contradictory evidence can coexist.
- **Suggested commits:** duplicate/diversity policy; selector; privacy/traceability tests/docs.

### [ ] T052: Implement Parquet analytical export

- **Suggested branch:** `feat/parquet-export`
- **Depends on:** T050
- **PRD:** US-011, FR-023
- **Scope:** Export versioned review facts, current classifications/aspects, and aggregates in bounded chunks.
- **Acceptance:**
  - Export can be rebuilt entirely from SQLite.
  - Schemas and partition policy are documented.
  - Round-trip tests preserve IDs, nulls, numbers, and timestamps.
  - Low-volume languages do not generate pathological tiny-file partitioning.
- **Suggested commits:** export schemas; chunked writer; round-trip tests/docs.

## 9. Phase 6 — Stage 2 and strategy brief

### [ ] T060: Define Stage 2 evidence payload and output schema

- **Suggested branch:** `feat/stage2-schema`
- **Depends on:** T050, T051
- **PRD:** US-012, FR-044, FR-045
- **Scope:** Build bounded synthesis input, structured output, evidence references, confidence/counterevidence, and hard item limits.
- **Acceptance:**
  - Invalid metric references and missing denominators fail validation.
  - Recommendations require action, impact, effort, evidence, and validation step.
  - Schema enforces configured list limits.
  - Insufficient-evidence output is valid.
- **Suggested commits:** input payload; output models/schema; validation tests/docs.

### [ ] T061: Implement cross-competitor synthesis

- **Suggested branch:** `feat/stage2-synthesis`
- **Depends on:** T040, T060
- **PRD:** US-012
- **Scope:** Add versioned Stage 2 prompt, provider call, bounded retries, structured persistence, lineage, and cost controls.
- **Acceptance:**
  - Stage 2 receives aggregates/evidence only, not the raw corpus.
  - Every cited review ID and metric resolves locally.
  - Unsupported geography claims fail post-validation or are removed through bounded repair.
  - Request ceilings produce resumable partial status.
- **Suggested commits:** prompt/payload builder; synthesis executor/storage; claim/lineage tests/docs.

### [ ] T062: Render the concise Markdown strategy brief

- **Suggested branch:** `feat/strategy-report`
- **Depends on:** T060, T061
- **PRD:** US-012, NFR-008
- **Scope:** Deterministically render executive direction, competitor grid, expectations, vulnerabilities, ranked priorities, positioning/store strategy, and risks.
- **Acceptance:**
  - Rendering performs no model call.
  - Default output respects configured item limits and target word range or returns an explicit validation error.
  - Links/IDs permit local evidence lookup.
  - Snapshot tests cover missing evidence, one competitor, and several competitors.
- **Suggested commits:** renderer; limit/word validation; snapshots and README examples.

### [ ] T063: Orchestrate full CLI pipeline

- **Suggested branch:** `feat/pipeline-cli`
- **Depends on:** T023, T024, T043, T050, T061, T062
- **PRD:** US-013, FR-050–FR-052
- **Scope:** Connect stage commands and `run`, stage selection, status, exit behavior, idempotency, and interruption handling.
- **Acceptance:**
  - An offline fixture project completes end to end with deterministic provider fixtures.
  - Re-running unchanged work performs no duplicate crawl/classification writes.
  - Partial app/batch failure is visible and resumable.
  - Human and JSON status agree.
- **Suggested commits:** stage commands; full runner; end-to-end tests/docs.

## 10. Phase 7 — Hardening and MVP release

### [ ] T070: Add observability and operational diagnostics

- **Suggested branch:** `feat/observability`
- **Depends on:** T063
- **PRD:** US-013, NFR-005
- **Scope:** Structured logs, run diagnostics, crawl/model usage metrics, redaction, and failure summaries.
- **Acceptance:**
  - Logs include run/app/unit context and exclude secrets/full review text by default.
  - Stop reasons and partial states are diagnosable from local output.
  - Redaction tests cover configured provider secrets and URL credentials.
- **Suggested commits:** logging context/redaction; metrics; diagnostics CLI/docs.

### [ ] T071: Complete security, privacy, and prompt-injection review

- **Suggested branch:** `hardening/security-review`
- **Depends on:** T063, T070
- **PRD:** NFR-005, risks
- **Scope:** Review untrusted inputs, SQL parameters, path handling, provider payloads, exports, logs, and secrets.
- **Acceptance:**
  - Adversarial reviews cannot change prompt instructions or output schema.
  - Standard exports omit reviewer profile identity.
  - No tracked fixture contains credentials or unnecessary personal identifiers.
  - Findings and fixes are documented in existing docs, not a standalone change-summary file.
- **Suggested commits:** tests/findings fixes by subsystem; final guardrail docs.

### [ ] T072: Benchmark scale and tune defaults

- **Suggested branch:** `perf/scale-baseline`
- **Depends on:** T063, T070
- **PRD:** NFR-004, performance expectations
- **Scope:** Exercise generated 100k and 1M review projects, measure database growth, ingest/query/aggregate time, and bounded memory; tune indexes and transaction sizes.
- **Acceptance:**
  - Reproducible benchmark command and hardware/context are documented.
  - No full-corpus in-memory requirement is observed.
  - Index changes include before/after evidence and migration tests.
- **Suggested commits:** benchmark harness; measured tuning; documented baselines.

### [ ] T073: Run live Steam and provider smoke validation

- **Suggested branch:** `test/live-smoke-validation`
- **Depends on:** T063, T071
- **PRD:** success metrics
- **Scope:** Run bounded live collection against approved public app IDs and bounded provider classification/synthesis using configured credentials.
- **Acceptance:**
  - Live test is opt-in and cost/rate limited.
  - No captured secret or unnecessary personal data is committed.
  - Failures become sanitized fixtures or targeted tests where permitted.
  - README records exactly how to repeat the smoke test.
- **Suggested commits:** smoke command/config; discovered compatibility fixes; docs.

### [ ] T074: Prepare MVP release candidate

- **Suggested branch:** `release/mvp-readiness`
- **Depends on:** T044, T071, T072, T073
- **PRD:** all MVP success metrics
- **Scope:** Verify installation, migrations, full fixture pipeline, evaluation thresholds, docs, examples, packaging, and clean-room setup.
- **Acceptance:**
  - Every PRD MVP story is mapped to passing tests or an explicit accepted limitation.
  - Fresh setup follows README only.
  - AGENTS commands match CI and local tooling.
  - PLAN statuses and deferred work are accurate.
  - Release notes summarize user-visible behavior and known constraints.
- **Suggested commits:** readiness fixes grouped by subsystem; final docs/release metadata.

## 11. Optional post-MVP epics

These are intentionally not decomposed until MVP evidence exists:

- **Stage 3 own-product alignment:** Ingest the user's Steam app and/or product documents to create an evidence-backed improvement roadmap.
- **Additional model providers:** Add native adapters only when capability or economics justify them.
- **Scheduled monitoring:** Automate periodic incremental refresh and strategy deltas.
- **Interactive evidence browser:** Add UI only after CLI workflows and evidence contracts stabilize.
- **Additional publishers/stores:** Generalize source contracts after Steam behavior is proven.

Each optional epic requires its own PRD update, specification change, branch set, and implementation tasks.

## 12. Cross-cutting verification matrix

Every feature owner should check applicable rows before handoff:

| Concern | Required evidence |
|---|---|
| Idempotency | Repeating the operation creates no duplicate logical state |
| Resumability | Injected interruption resumes from persisted progress |
| Lineage | Output identifies source, configuration, and schema/model versions |
| Privacy | Standard output excludes unnecessary profile identifiers |
| Security | Untrusted content is data; secrets are redacted |
| Sampling | Population, seed, strata, coverage, and weights are recorded |
| Metrics | Numerator, denominator, and population definition are present |
| Documentation | README/SPEC/PLAN/config examples reflect behavior |
| Git hygiene | Dedicated branch and multiple coherent commits for major work |
| Offline feedback | Unit/integration/contract tests avoid required network access |

## 13. Immediate starting point

Implementation should start with **T001: Bootstrap the Python package and feedback loop** on branch `chore/python-bootstrap`. Do not begin Steam scraping before configuration, domain contracts, and test infrastructure exist; those foundations prevent later features from inventing incompatible run, error, and configuration behavior.
