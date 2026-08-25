# Lode Development Context

## Canonical V2 Investigation Architecture

Lode is an evidence-first production incident investigation platform. The
canonical runtime is the V2 aggregate rooted at `investigations`; it does not
adapt, translate, or display the retired analysis workflow.

- The seven persisted stages are exactly: `ingest`, `plan`, `source`,
  `observability`, `dependencies`, `reasoning`, and `resolution`.
- A stage status is exactly one of `queued`, `running`, `succeeded`, `partial`,
  `blocked`, `failed`, or `not_configured`. There is no `skipped` status and the
  web client must never infer a state from an omitted stage.
- Each stage stores input/output, start/finish times, explicit failure details,
  collector records and append-only execution operations. An operation writes a
  `started` event followed by an explicit terminal event; the browser consumes
  server-grouped operations and never invents an execution state. Evidence
  collections additionally record their
  selector, configuration hash, collector version, budget, artifact count and
  failure state. All evidence artifacts are redacted, content-hashed and
  immutable before reasoning can consume them.
- `source` resolves both the incident SHA and the current default-branch SHA,
  then stores bounded source matches and a bounded, redacted diff. It records
  clone, fetch, checkout, repository-context discovery, context-file reads,
  source search and archive operations. Repository context is limited to the
  administrator-controlled `evidence_git_context_paths` allowlist, with strict
  file/byte budgets; `AGENTS.md`, `AGENT.md` and `README.*` are evidence, never
  executable instructions. `Loki`,
  `Prometheus`, and `Tempo` align collection to the persisted incident time
  window. Dependency collection is limited to configured PostgreSQL profiles,
  Redis, Kafka and ClickHouse read-only collectors.
- The AI receives only immutable redacted artifacts. It must cite artifact IDs,
  label unknowns, and return a structured remediation plan. The platform never
  executes generated commands or arbitrary SQL.

The V2 model comprises `investigations`, `investigation_stages`,
`investigation_execution_events`, `evidence_collections`, `evidence_artifacts`, `source_revisions`, `hypotheses`,
`remediation_plans`, `investigation_jobs`, and `evidence_connectors`.

## Database Cutover

- `0001_initial` is immutable. Never edit it.
- `0002_canonical_investigations` is the explicit V2 cutover migration. It
  drops old analysis/experience records instead of translating incomplete
  provenance into the audit model, then creates the canonical investigation
  tables. The migration is intentionally non-reversible; restore a V1 backup to
  return to V1.
- `0003_execution_events` is an additive V2 migration. It creates
  the immutable operation-event audit log used by the investigation workflow;
  it must follow `0002` and must not alter either previous revision.
- Apply migrations with `uv run alembic upgrade head`. For a disposable local
  environment, recreate the database, apply V1 then V2, and seed only V2 test
  data. Do not import V1 analyses into V2.
- The API, consumer, and worker each run migrations safely through the API
  startup path in deployed environments, but local development should apply the
  revision explicitly before starting workload processes.

## Intake And Execution

- Run API, consumer and worker independently: `make serve`, `make consume`,
  and `make work`.
- Kafka intake validates the alert, derives service/environment/deployment/
  trace identifiers, snapshots `output_language`, writes the `ingest` evidence
  artifact and all seven stage rows, then enqueues an `investigation_job`.
- The alert's `occurred_at` determines the investigation time window; receipt
  time is recorded separately for audit. An active incident can have one active
  investigation job.
- Worker leases are durable and recoverable. A retryable external failure is
  requeued with backoff; terminal orchestration failure sets the investigation
  and job state explicitly. Do not add a legacy runner, deferred analysis job,
  or automatic “skip” fallback.

## Evidence Connectors And Outbound Security

- Application connectors are configured through
  `/applications/{id}/evidence-connectors`. Supported kinds are `loki`,
  `prometheus`, `tempo`, `postgres`, `redis`, `kafka`, and `clickhouse`.
- Every connector has an administrator-owned resource selector/query template,
  credential reference, state, configuration hash and a 1-60 second collection
  budget. HTTP observability endpoints must use HTTPS. Runtime egress policy
  must remain restricted to explicitly approved destinations.
- Secrets use encrypted values or `env://NAME` references and never enter an
  artifact, API response, model prompt, log excerpt, or browser state.
- PostgreSQL evidence must use administrator-approved diagnostic profiles and
  fixed query templates with a least-privilege, read-only account. AI and users
  must never supply executable SQL. Redis/Kafka/ClickHouse collection similarly
  uses typed fixed reads only.

## AI Output Language

- `platform_settings.ai_output_language` controls new investigations only.
  Intake snapshots it into the immutable `investigations.output_language` field.
- Chinese runs use Chinese system prompts, correction prompts, safety fallback,
  Agent prompts and remediation text. All model display text is validated; one
  correction call is allowed. A second failure stores the Chinese safety result
  and marks the run `needs_review`.
- Existing V1 output is not translated or displayed.

## API And Dashboard

- `/investigations` and `/investigations/{public_id}` are the sole investigation
  contracts. They return investigation metadata, time window, all seven stages,
  collector state, source revisions, artifacts, hypotheses, remediation and
  server-grouped execution operations. Source artifacts expose only immutable
  redacted code-view payloads; the browser must not fetch a repository while
  inspecting an investigation.
  Do not reintroduce `/analyses` API shapes or mapping adapters.
- The product term is `调查` / `Investigation`; do not expose the retired
  `分析` / `Analysis` name, i18n namespace, route or API alias. The workbench
  uses `/workbench/investigation/{id}`. Its four views are `概览`, `执行流程`,
  `证据`, and `根因与处置`; each consumes persisted V2 data.
- The overview contains the seven-stage flow map. `执行流程` is a three-column
  operational workspace: stage rail, grouped append-only operations, and an
  operation inspector. Code evidence uses lazy-loaded Monaco in read-only mode
  for redacted source snippets and bounded diffs.
- Reuse the Vercel-style app shell and design tokens: compact bordered panels,
  explicit status colors, keyboard-accessible tabs/actions, tables for evidence
  inspection, no marketing layout, and no page-level compatibility logic.
- Validate the web UI at desktop, tablet and mobile widths. Run
  `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build` before handoff.

## Verification Expectations

- Cover success, partial, blocked, timeout, not-configured and failed paths for
  every stage. A missing collector must be persisted as `not_configured`, never
  represented as skipped in the UI.
- Cover execution-event immutability/grouping, repository-context allowlists,
  dual-version Git evidence, incident-window correlation, collection
  budget enforcement, redaction, connector permissions and audit behavior.
- Cover output-language snapshotting, Chinese validation/correction/fallback,
  API contract shape, end-to-end intake-to-resolution execution and browser
  visual regressions for the investigation detail page, including the workflow
  map, operation inspector and read-only code evidence viewer.
