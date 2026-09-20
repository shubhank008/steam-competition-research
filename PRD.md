# Product Requirements Document

## 1. Product overview

Steam Competition Research is a CLI application that converts Steam store pages and user reviews into a concise, evidence-backed competitive strategy brief. A user creates a research project, adds any number of Steam apps or games as competitors, collects their public store metadata and reviews, classifies actionable review evidence, and synthesizes the results into a cross-competitor product and marketing plan.

The system is general-purpose. It ships with a universal Steam taxonomy and allows each research project to extend that taxonomy for a domain such as desktop mascots. It preserves source data and analytical lineage so findings can be reproduced, audited, and regenerated when prompts, models, or taxonomy definitions change.

## 2. Problem

Steam competitor research is difficult to perform reliably at scale:

- Store metadata and large review corpora are distributed across HTML pages and paginated endpoints.
- Individual reviews often contain several product signals, while simple summaries discard minority views and evidence.
- Review language, playtime, recency, polarity, and helpfulness materially affect interpretation.
- Large language models cannot ingest hundreds of thousands of raw reviews in one useful, reproducible request.
- Generic research reports are lengthy but fail to rank concrete product, technical, and positioning decisions.

The product must separate collection, evidence extraction, deterministic aggregation, and strategy synthesis so each stage can be resumed, inspected, and rerun independently.

## 3. Goals

- Collect and incrementally refresh complete Steam review corpora using durable cursor checkpoints.
- Capture localized Steam store metadata for a configurable country and language.
- Preserve complete source review objects while making important fields queryable in SQLite.
- Extract multiple evidence-backed product aspects from each eligible review using a configurable LLM provider.
- Discover review languages dynamically and compare language-market patterns without claiming reviewer geography.
- Produce deterministic cross-competitor metrics with explicit denominators and source lineage.
- Generate a concise strategy brief that ranks product gaps, technical risks, feature priorities, and positioning opportunities.
- Support project-specific taxonomy extensions without database migrations or code changes.
- Keep recurring runs incremental so unchanged reviews do not consume classification budget again.

## 4. Non-goals

The initial release will not:

- Provide a graphical or hosted user interface.
- Publish reports to Notion or another external workspace.
- Infer reviewer location from review language.
- Treat negative sentiment, low playtime, or self-reported abandonment as verified churn.
- Scrape private Steam data or require reviewer authentication.
- Automatically modify a product roadmap or Steam store listing.
- Implement the future own-product alignment stage described in Section 12.
- Guarantee compatibility with undocumented Steam behavior indefinitely; response fixtures and adapter tests will detect changes.

## 5. Target users

### Primary user

A product owner, solo developer, researcher, or small game studio evaluating competitors before making product, technical, pricing, or marketing decisions.

### Initial operating mode

A technically capable user runs commands locally, supplies Steam store URLs or app IDs, configures model credentials, and reads local Markdown and structured output files.

## 6. Core concepts

- **Research project:** A collection of competitor apps analyzed together under one configuration and taxonomy.
- **Competitor:** A Steam app or game included in a research project.
- **Crawl run:** One attempt to retrieve store metadata and/or reviews.
- **Classification run:** A versioned Stage 1 operation that converts review text into structured product evidence.
- **Language market:** Reviewers writing in a given language. It is not a geographic assertion.
- **Early-friction cohort:** Reviews with `playtime_at_review < 120` minutes.
- **Established-use cohort:** Reviews with `playtime_at_review` from 120 through 2,999 minutes.
- **Long-use cohort:** Reviews with `playtime_at_review >= 3000` minutes.
- **Strategy brief:** The concise Stage 2 cross-competitor output intended to guide decisions.

## 7. User stories

### US-001: Initialize a research project

**Description:** As a researcher, I want to initialize a project with versioned configuration so that every run uses explicit and reproducible settings.

**Acceptance criteria:**

- [ ] The CLI creates a project configuration, local data directories, and SQLite database at user-selected or documented default paths.
- [ ] The configuration identifies the project, country, store language, taxonomy profile, storage paths, and model profiles.
- [ ] Resolved configuration is recorded in a run manifest whenever a pipeline stage executes.
- [ ] Secrets are read from environment variables and are not written into manifests.

### US-002: Manage the competitor set

**Description:** As a researcher, I want to add Steam store URLs or app IDs so that I can define the products compared by the project.

**Acceptance criteria:**

- [ ] The CLI accepts a valid Steam store URL or numeric app ID.
- [ ] Duplicate app IDs are rejected without creating duplicate records.
- [ ] The competitor list can be displayed from the CLI.
- [ ] An invalid URL or app ID produces a clear error without changing project state.

### US-003: Collect store metadata

**Description:** As a researcher, I want localized store metadata for each competitor so that positioning and merchandising can be compared alongside reviews.

**Acceptance criteria:**

- [ ] The system captures title, short description, header/capsule image, screenshots and thumbnails, total reviews, review summary, release date, developer, publisher, popular tags, price, full description sections, and more-like-this entries when available.
- [ ] Every snapshot records app ID, country, language, currency when known, fetch time, source URL, adapter, and parser schema version.
- [ ] Missing optional fields are reported as extraction warnings rather than causing silent data loss.
- [ ] The normal HTTP adapter is attempted before the browser fallback according to documented fallback conditions.

### US-004: Crawl complete review corpora

**Description:** As a researcher, I want all available positive and negative reviews stored locally so that analysis is not constrained by Steam page summaries.

**Acceptance criteria:**

- [ ] Positive and negative streams may run concurrently and maintain independent URL-encoded cursors.
- [ ] Each stream paginates sequentially with up to 100 reviews per request.
- [ ] Every review is upserted by `(appid, recommendationid)` and its complete source object is retained.
- [ ] Empty pages, repeated cursors, non-progressing cursors, configured limits, and retry exhaustion terminate safely with an explicit status.
- [ ] Interrupted streams can resume from durable checkpoints.
- [ ] `0` as a configured maximum means no user-imposed review limit.

### US-005: Incrementally refresh reviews

**Description:** As a researcher, I want recurring crawls to process new and changed reviews first so that refreshes are efficient and safe.

**Acceptance criteria:**

- [ ] Incremental crawling uses Steam's `updated` ordering.
- [ ] Existing review IDs are still upserted when their source record changes.
- [ ] A stream stops only after satisfying the configured overlap/high-water rule, not upon the first duplicate.
- [ ] High-water state advances only after successful stream completion.
- [ ] A review that changes recommendation polarity remains one canonical review record.

### US-006: Explore raw and normalized reviews

**Description:** As a researcher, I want source-complete reviews in a queryable datastore so that I can change filters and analytical methods without scraping again.

**Acceptance criteria:**

- [ ] SQLite contains typed columns for commonly filtered Steam fields and the complete review object as JSON.
- [ ] Users can query review counts by competitor, language, polarity, time, and playtime cohort.
- [ ] API page metadata records request parameters, cursor progression, fetch result, and content hash.
- [ ] The datastore supports full-text review search when the local SQLite build includes FTS5.

### US-007: Filter low-information inputs without deleting evidence

**Description:** As a researcher, I want obvious noise excluded from LLM input while preserving every source review.

**Acceptance criteria:**

- [ ] Empty, whitespace-only, punctuation-only, repeated-character spam, and detected ASCII-art reviews are excluded from Stage 1.
- [ ] When the corpus exceeds the configured threshold, stricter short-review eligibility rules may apply.
- [ ] Short reviews matching configured high-signal vocabulary are retained for classification.
- [ ] Every exclusion records a reason and filtering-policy version.
- [ ] Excluded reviews remain available in SQLite and aggregate source counts.

### US-008: Configure the research taxonomy

**Description:** As a researcher, I want to extend the universal taxonomy for my product domain so that the classifier captures project-specific evidence.

**Acceptance criteria:**

- [ ] A project selects a versioned YAML or TOML taxonomy file through configuration or an environment override.
- [ ] The selected taxonomy extends the universal core without changing database columns.
- [ ] Invalid definitions, duplicate IDs, and missing required fields fail validation before classification begins.
- [ ] The merged taxonomy name, version, and content hash are recorded with each classification run.

### US-009: Classify actionable review evidence

**Description:** As a researcher, I want Stage 1 to extract every material aspect from eligible reviews so that aggregation does not discard multi-topic feedback.

**Acceptance criteria:**

- [ ] Each eligible input receives exactly one validated classification result or an explicit failure record.
- [ ] A result includes actionability, engagement posture, overall sentiment and intensity, multiple aspect records, evidence excerpts, requested features, use cases, and confidence.
- [ ] Review text is treated as untrusted data and cannot alter classifier instructions.
- [ ] Batches are constrained by token budget as well as review count.
- [ ] Malformed responses follow a bounded repair, split, and quarantine policy.
- [ ] Model, provider, prompt, taxonomy, parameters, and source review version are recorded.

### US-010: Control classification coverage and cost

**Description:** As a researcher, I want explicit classification scopes so that large corpora can be analyzed within a chosen model budget.

**Acceptance criteria:**

- [ ] The CLI supports `all`, `progressive`, `stratified`, and `unclassified-only` scopes.
- [ ] Corpora at or below the configured full-corpus threshold default to complete classification.
- [ ] Larger corpora default to reproducible progressive coverage across polarity, language, playtime, recency, and helpfulness.
- [ ] Sampling records the seed, inclusion reason, source population, and weights required for valid aggregate rates.
- [ ] Recurring runs default to reviews that are new, changed, failed, or obsolete under current classifier versions.
- [ ] Per-run request, token, and cost ceilings stop cleanly without marking incomplete work as complete.

### US-011: Build deterministic competitor metrics

**Description:** As a researcher, I want metrics computed in code rather than invented by the synthesis model so that findings are auditable.

**Acceptance criteria:**

- [ ] Aggregation reports counts, rates, denominators, and sample coverage.
- [ ] Metrics can be segmented by competitor, language, Steam polarity, playtime cohort, review period, aspect, and engagement posture.
- [ ] Language groups below the configured minimum sample size are marked as insufficient evidence.
- [ ] Unweighted prevalence and visibility-weighted impact are reported separately where helpfulness is used.
- [ ] Representative quotes retain recommendation IDs and are selected using a documented diversity policy.
- [ ] Aggregates can be exported to Parquet without becoming the operational source of truth.

### US-012: Generate a concise cross-competitor strategy brief

**Description:** As a product owner, I want a short ranked strategy brief so that I can decide what to build, avoid, and communicate.

**Acceptance criteria:**

- [ ] Stage 2 consumes aggregate metrics and selected evidence rather than the full raw corpus.
- [ ] The canonical output is validated structured JSON and is rendered to Markdown without another model call.
- [ ] The default Markdown brief is targeted at 1,500–3,000 words and hard-limits list sizes.
- [ ] It contains executive direction, a competitor grid, table stakes, competitor vulnerabilities, ranked product priorities, positioning/store strategy, and risks/counterevidence.
- [ ] Recommendations include supporting metrics and denominators, affected competitors, evidence IDs, confidence, expected impact, effort, and a validation action.
- [ ] The model reports insufficient evidence instead of manufacturing conclusions.
- [ ] Language-market findings never claim known reviewer geography.

### US-013: Resume and inspect pipeline runs

**Description:** As a CLI user, I want stage-level status and resumability so that long crawls or classifications survive interruption.

**Acceptance criteria:**

- [ ] Every stage records pending, running, completed, partial, or failed status.
- [ ] The CLI displays per-app crawl and classification progress.
- [ ] Rerunning a completed idempotent stage does not duplicate records.
- [ ] A failed app or batch does not erase successful work from other units.

## 8. Functional requirements

### Project and configuration

- **FR-001:** The system shall organize competitors, runs, outputs, and configuration under a research project.
- **FR-002:** Configuration precedence shall be CLI arguments, environment variables, project configuration, then documented defaults.
- **FR-003:** Environment variables shall hold secrets and may override scalar deployment settings; structured taxonomies shall use versioned YAML or TOML files.
- **FR-004:** Every stage shall persist a sanitized resolved-configuration manifest.

### Collection

- **FR-010:** The system shall expose separate store-page and review-source interfaces.
- **FR-011:** The initial store-page adapters shall be `curl_cffi` and Patchright, with HTTP preferred.
- **FR-012:** Store requests shall use configurable country and language values, defaulting to `US` and `english`.
- **FR-013:** Review collection shall use `filter=updated`, `language=all`, configurable purchase type, and configurable off-topic filtering.
- **FR-014:** Positive and negative review streams shall maintain independent crawl state.
- **FR-015:** Retries shall use bounded exponential backoff with jitter for retryable transport and server failures.
- **FR-016:** Review maximums shall have documented per-app and/or per-stream semantics and be deterministic under concurrency.

### Storage

- **FR-020:** SQLite shall be the canonical operational datastore for projects, competitors, source reviews, runs, checkpoints, classifications, and aggregates.
- **FR-021:** The normalized review record shall preserve the complete source review JSON.
- **FR-022:** A single project database shall support cross-competitor queries.
- **FR-023:** Parquet files shall be rebuildable analytical exports, not canonical state.
- **FR-024:** Stage 2 JSON and rendered Markdown shall both be retained.

### Classification

- **FR-030:** The system shall provide an OpenAI-compatible LLM adapter with explicit provider capability reporting.
- **FR-031:** Stage 1 and Stage 2 shall allow independent provider, model, parameters, and prompts.
- **FR-032:** The universal taxonomy shall support technical stability/performance, compatibility/platform, usability/accessibility, content/features, customization/modding, monetization/value, multiplayer/community, support/update quality, use cases, requested features, and engagement posture.
- **FR-033:** Project taxonomies shall add hierarchical categories dynamically.
- **FR-034:** Stage 1 shall use a strict machine-validated structured-output contract.
- **FR-035:** Classifications shall become obsolete when their source review, prompt, taxonomy, or configured model policy changes.

### Aggregation and synthesis

- **FR-040:** Aggregate calculations shall execute deterministically in SQL or Python.
- **FR-041:** Early-friction, established-use, and long-use cohorts shall use configurable minute thresholds with the documented defaults.
- **FR-042:** API `refunded` shall be treated as a source fact but not as the primary retention metric.
- **FR-043:** Self-reported refund, uninstall, or abandonment statements shall be labeled unverified if retained as evidence.
- **FR-044:** Stage 2 shall compare all selected competitors using one compatible taxonomy and explicit coverage metadata.
- **FR-045:** The report shall distinguish observed evidence, interpretation, recommendation, and counterevidence.

### CLI and exports

- **FR-050:** The CLI shall support project initialization, competitor management, crawling, classification, aggregation, synthesis, status, full pipeline execution, and export.
- **FR-051:** Commands shall return non-zero exit codes for failed requested operations and explain partial completion.
- **FR-052:** Machine-readable status and outputs shall be available for future automation.

## 9. Non-functional requirements

- **NFR-001 — Reproducibility:** Each finding must resolve to a project, source crawl, source reviews, filtering policy, taxonomy, prompt, model, and aggregate version.
- **NFR-002 — Idempotency:** Repeating a successful operation with unchanged inputs must not create duplicate logical records.
- **NFR-003 — Resumability:** Long-running review and classification operations must persist progress frequently enough to avoid restarting completed pages or batches.
- **NFR-004 — Scale:** The design must support millions of review rows per project without one-file-per-review storage.
- **NFR-005 — Safety:** HTML, review text, and source metadata must be treated as untrusted input. Secrets must not appear in logs, manifests, prompts, or version control.
- **NFR-006 — Auditability:** Published percentages must include denominators and distinguish full-corpus from sampled estimates.
- **NFR-007 — Extensibility:** A new fetcher, model provider, taxonomy profile, or exporter must be addable behind a narrow interface.
- **NFR-008 — Concision:** The default strategy brief must remain within its configured word and item limits.
- **NFR-009 — Testability:** Network adapters must be testable against committed sanitized fixtures; core storage, filtering, classification validation, and aggregation must be testable without network access.
- **NFR-010 — Portability:** The CLI must support common current Linux, macOS, and Windows environments where its dependencies are available.

## 10. Success metrics

The MVP is successful when:

- A new user can create a project, add at least three competitors, and run the documented pipeline from the CLI.
- A deliberately interrupted review crawl resumes without duplicate logical reviews or loss of completed work.
- Every reported percentage in a generated strategy brief maps to a stored aggregate with a denominator.
- Every quoted review maps to a stored recommendation ID.
- Re-running an unchanged project in `unclassified-only` mode sends no already-current reviews to Stage 1.
- A taxonomy extension can be activated through configuration without a schema migration.
- The generated default report stays within configured length limits and ranks no more than the configured number of priorities.
- A manually labeled multilingual evaluation corpus demonstrates acceptable thresholds defined before production model selection for schema validity, omission rate, actionability, and aspect precision/recall.

## 11. Risks and mitigations

| Risk | Consequence | Mitigation |
|---|---|---|
| Steam response or page markup changes | Collection or parsing fails silently | Version parsers, retain fixtures, emit extraction warnings, and test field coverage |
| Cursor streams mutate during long crawls | Reviews are missed or repeated | Upsert by recommendation ID, checkpoint cursors, and use overlap-based incremental stopping |
| Review language is mistaken for location | Unsupported regional claims | Use language-market terminology and explicit report constraints |
| LLM omits reviews or aspects | Biased aggregate results | Stable input IDs, exact output cardinality validation, repair/split/quarantine policy |
| Prompt injection inside reviews | Classifier behavior is altered | Delimit untrusted text, prohibit instruction following, validate output schema |
| Sampling distorts prevalence | Misleading priorities | Reproducible stratification, coverage metadata, weights, and full-corpus mode |
| Old issues dominate current strategy | Obsolete recommendations | Segment by review period and surface recent versus lifetime evidence |
| Provider behavior differs behind compatible APIs | Runtime and validation failures | Capability-aware adapters and provider contract tests |
| Strategy report becomes exhaustive | Decisions are obscured | Hard word/item limits and ranked outputs |

## 12. Future direction: own-product alignment

A later optional Stage 3 may ingest the user's own Steam app, processed reviews, feature inventory, MVP, specification, or game design document. It would compare market opportunities against current product capability and first-party feedback to recommend roadmap, quality, store metadata, pricing, and positioning changes.

The MVP shall not implement Stage 3, but Stage 1 and Stage 2 artifacts must be structured and traceable so they can become Stage 3 inputs without rescraping or reclassifying competitors.

## 13. Open questions for implementation discovery

These questions do not block the documentation foundation, but the owning feature branch must resolve and document them before implementation:

- Which Python versions and package manager will the project officially support?
- Which OpenCode Go endpoint, model identifiers, structured-output capabilities, limits, and retention terms will be used initially?
- Which Steam fields require HTML extraction versus a stable structured endpoint?
- What evaluation thresholds qualify a Stage 1 model for release?
- What exact default sample ceilings and minimum language sample sizes provide the best first-run cost/coverage tradeoff?
- Should immutable full API page bodies be retained indefinitely, compressed in SQLite, or governed by a configurable retention policy?

## 14. Documentation and delivery policy

Documentation is part of every feature, not follow-up work.

- Every behavior or configuration change must update the relevant README, PRD, specification, plan, example configuration, and/or agent guidance in the same branch.
- Every major feature or material change must be developed on its own branch from an up-to-date default branch.
- Work must be committed regularly at coherent checkpoints. Avoid a single large commit after a long editing session.
- Feature branches should remain focused, independently reviewable, and safe to abandon or revert.
- A feature is not complete while its implementation and documentation disagree.
