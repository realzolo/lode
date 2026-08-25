# Lode Project Context

Lode is an evidence-backed production incident investigation service. The V1 architecture is intentionally serial within each investigation and has no historical execution-path adapters. Different investigations may execute concurrently.

## Runtime Components

- `src/lode/api`: FastAPI control plane, authorization, manual intake, investigation detail, audit pagination, and SSE.
- `src/lode/consumer`: strict Kafka `alert.v1` validation and shared intake dispatch.
- `src/lode/worker`: durable job claiming, investigation-scoped leases, retry, and bounded cross-investigation concurrency.
- `src/lode/engine/investigation_intake.py`: canonical normalization, masking, immutable input evidence, and job creation.
- `src/lode/engine/investigation_engine.py`: one-action-at-a-time adaptive decision loop and terminal synthesis.
- `src/lode/engine/structured_outputs.py`: provider-neutral strict JSON Schemas for decisions, reports, and causal verification.
- `src/lode/engine/investigation_evidence.py`: serial Git and connector evidence collection.
- `src/lode/engine/evidence/git.py`: stack parsing, exact revision lookup, symbol range extraction, lexical candidates, and related-symbol expansion.
- `src/lode/engine/investigation_events.py`: durable step, operation, progress, failure, timing, and evidence events.
- `apps/web`: Next.js workbench with manual intake, serial execution track, final-finding code viewer, and paginated audit drawer.

PostgreSQL is the source of truth. Kafka consumers only validate and enqueue; workers execute investigations. Model configuration is selected from the application binding first, then the global default. The global default does not satisfy the application activation requirement described below.

## Application Activation

Every transition into active Kafka ingestion fails closed unless the application has all three required bindings:

1. at least one `ApplicationRepo`;
2. one non-empty `ApplicationKafka.topic`;
3. one existing application-level `model_config_id` whose protocol availability test passed.

Both initial start and resume call the same backend readiness gate. Initial start validates broker visibility only after the three configuration checks pass. Missing configuration returns HTTP 409 using the canonical business-error envelope:

```json
{
  "error": {
    "code": "application_not_ready",
    "message": "Complete all required application settings before starting ingestion.",
    "details": {"missing": ["repositories", "topic", "model_availability"]}
  }
}
```

`GET /applications` exposes `repo_count`, `model_configured`, and `model_available`; the Web start dialog displays all requirements and cannot submit while its current application snapshot is incomplete. Binding a model performs a live protocol probe before persisting the binding. `POST /settings/ai-models/{id}/test` repeats the probe on demand. Editing endpoint, key, provider, or model resets health to `untested`. The backend gate remains authoritative for stale clients and direct API callers.

## Investigation Execution

An investigation owns exactly one execution lane:

1. Canonicalize and mask the complete error input.
2. Parse stack frames and the structured error contract.
3. Build or update the leading mechanism.
4. Select one action ID from the server-generated catalog.
5. Execute and await that action before selecting another.
6. Archive evidence, facts, counter-evidence, and validation gaps.
7. Synthesize separate incident and code results.
8. Persist one terminal decision and report.

Do not add `asyncio.gather`, overlapping connector calls, or task-local parallelism to the investigation path. Blocking Git or HTTP work may run in a thread pool only when immediately awaited. `engine_concurrency` controls how many separate investigations workers may run.

Limits are configured by:

- `investigation_max_evidence_steps`, default 12;
- `investigation_max_model_calls`, default 10;
- `investigation_timeout_seconds`, default 600.

Every action uses a server-generated fingerprint and may run once. Steps and operations commit independently. PostgreSQL partial unique indexes allow no more than one running step and one running operation for an investigation. On worker recovery, completed evidence is reused and the first unfinished durable action resumes. Lease cleanup only changes jobs whose own leases expired.

Model output may select catalog IDs and cite archived evidence IDs. It may never create commands, SQL, URLs, repository paths, connector configuration, or credentials.

OpenAI-compatible base URLs are normalized to `/v1/chat/completions`; Anthropic base URLs are normalized to `/v1/messages`. Investigation requests use a configurable 120-second per-attempt timeout, provider-enforced strict JSON Schemas, and an 8192-token output bound; health probes keep a separate 30-second timeout. OpenAI-compatible providers use `response_format.json_schema`, while Anthropic uses a forced schema-bound tool result. Calls automatically retry only transient network failures, timeouts, HTTP 429, and HTTP 5xx with bounded exponential backoff. Authentication, request validation, and non-JSON protocol responses fail immediately. Each retry emits operation progress, and AI audit rows retain the actionable error classification and actual attempt count. Do not collapse a timeout or protocol error into a generic "model unavailable" message. If both the initial structured response and the single repair fail validation, report the analysis as unavailable with the exact contract error; never relabel output-format failure as insufficient evidence.

## Input Contract

Kafka accepts strict `alert.v1`. The important deployment and error fields are:

- top-level `version` and full `git_commit`;
- `occurred_at`, `event_type`, severity, title, dedupe key and TTL;
- business `fields` including trace and provider-specific error codes;
- `error_log.name`, `message`, full `stack`, recursive `cause`, and `properties`.

Object errors often arrive with `error_log.name = "object"`, a JSON string in `message`, and the real response in `properties.value`. Normalization promotes structured code/message values for diagnosis while retaining the complete masked wire representation. Do not discard a null stack or replace structured properties with a generic message.

Manual `POST /investigations` accepts the same logical fields plus bounded attachments. It requires application `analyze` permission and calls the same normalization service as Kafka.

## Source Investigation

Source lookup order is fixed:

1. Exact file, line, class, and function from the stack at the incident revision.
2. Incident-revision symbols and structured error identifiers.
3. Error code definitions and references.
4. Caller, callee, error branch, exception conversion, return checks, timeout, retry, and related tests.
5. Resolve versions independently per repository. Try the alert deployment ref first; if it does not exist in a bound repository, resolve and freeze that repository's default branch HEAD as its incident baseline. If no deployment ref exists, use the default branch directly.

Generated build directories are excluded. Project documentation provides vocabulary and repository context only. A file read, path match, error-code match, or README excerpt is never code-cause proof.

The context package always retains normalized input and error structure. Evidence is ordered by causal relevance instead of a simple first-N slice.

## Result And Code Contracts

Investigation result states are `pending`, `confirmed`, `hypothesis`, `insufficient`, and `unavailable`. There is no overall confidence number.

- `confirmed` requires at least one incident-version code finding that passes all structural gates and an independent semantic verification pass.
- `hypothesis` contains one supported mechanism, exact candidate code when available, counter-evidence, and a validation method.
- `insufficient` contains no invented cause.
- `unavailable` reports missing required capability or repeated strict-output contract failure without a fabricated fallback conclusion. Output contract failure explicitly states that it does not prove evidence insufficiency.

Non-confirmed reports may request evidence and propose tests. They may not prescribe a production code change.

Reports always separate:

- `incident_cause`: the actual incident mechanism, including an external service, network, data, or infrastructure failure;
- `code_diagnosis`: a project defect, a resilience gap, `no_defect`, or `not_found`.

An `InvestigationCodeFinding` with `confirmed` or `hypothesis` must reference an immutable `source_file` artifact and exactly match its repository ID, full SHA, role, path, symbol, and line bounds. It explains faulty behavior, why it violates a contract, expected behavior, trigger, propagation, incident evidence, supporting evidence, counter-evidence, missing validation, and a test scenario.

For `confirmed`, the revision must be the immutable incident baseline and the location must be linked by stack, runtime/dependency evidence, or the archived alert contract. The baseline is resolved independently for every repository: use the deployment ref when that repository contains it; otherwise freeze the repository default branch HEAD and record `default_branch_after_unresolved_deployment`. When the alert has no deployment ref, record `default_branch_assumed_current`. The independent verifier must still prove the branch, trigger, and propagation. A default-branch match alone is never causal proof.

External causes can be represented accurately while code diagnosis separately reports no project defect or an exact timeout, retry, validation, fallback, or error-preservation weakness.

## API And Events

HTTP errors use one envelope: `error.code`, a string or HTTP status code; `error.message`, always a string; and optional structured `error.details`. Do not place structured objects in `error.message`.

`GET /investigations/{id}` returns the canonical fields: `input`, `report`, ordered `steps`, `decisions`, `operations`, `evidence`, and `code_findings`, plus retry lineage and archive state.

`POST /investigations/{id}/retry` is valid only for a non-archived terminal investigation. It live-tests the application-bound model, creates a new investigation from the immutable normalized input, and records `retry_of`; it never mutates or reuses the old run. `POST /investigations/{id}/archive` is valid only for a terminal run and permanently makes that run read-only. Archived runs remain available to detail, event, SSE, and audit reads.

`GET /investigations/{id}/audit` returns separately paginated operation and AI-call summaries. The audit UI must expose the full masked operation purpose, input, progress, result, timing, failure, metrics, events, and evidence references rather than a recent-event slice.

SSE event names are:

- `step.updated`
- `operation.started`
- `operation.progress`
- `operation.finished`
- `decision.recorded`
- `code_finding.updated`
- `report.updated`
- `investigation.finished`

Operation event rows have monotonically increasing sequence values and support replay. Clients treat SSE as an invalidation/event channel and reload canonical state from the detail API.

## Database

`alembic/versions/0001_initial.py` is the frozen V1 baseline and must never be edited after release. `0002_model_health_retry_archive.py` upgrades an existing V1 database with model health, AI attempt audit, retry lineage, and irreversible archive state. Do not add payload translation, parallel schemas, or old endpoint adapters.

The investigation-owned tables are inputs, steps, decisions, operations, operation events, evidence collections/artifacts/links, source revisions, findings/edges, code findings, AI invocations, reports, jobs, and connectors. Evidence content is immutable after archival. Connector secrets must be references or encrypted values and are masked in events and API output.

New model changes require a new forward migration. Verify both a V1-to-head upgrade and a fresh head schema:

```bash
LODE_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/lode_migration_test \
LODE_SECRET_KEY=test-secret \
uv run alembic upgrade 0001_initial
uv run alembic upgrade head
uv run alembic check
```

Alembic autogeneration against that database must produce no schema difference.

## Frontend Contract

The investigation workbench displays the incident cause first and code diagnosis second. Only source artifacts referenced by final validated code findings may appear in the main code viewer; lexical, context, and intermediate source candidates remain available only as masked audit references. The code viewer is read-only, follows the current application theme, and highlights the exact finding range alongside where it is wrong, why, trigger, propagation, expected behavior, missing validation, and test.

Execution uses a vertical serial track. Every operation exposes its purpose, masked input, progress events, actual result, duration, failure, metrics, and evidence links. Running states use restrained motion with a reduced-motion fallback. Initial data and Monaco loading surfaces reserve stable dimensions, and transient SSE refresh failures preserve the last canonical result instead of replacing the page. Waiting and running states have stable dimensions on desktop, tablet, and mobile.

Do not restore a second investigation UI or translate historical response shapes.

## Development And Verification

Backend:

```bash
uv sync --extra dev
uv run python -m compileall -q src scripts alembic tests
uv run pytest -q
```

Web:

```bash
cd apps/web
pnpm install
pnpm typecheck
pnpm build
```

For investigation changes, tests must cover strict alert parsing, full error preservation, exact stack/source range, per-repository default-branch fallback, candidate rejection, incident revision gates, strict model response schemas, independent semantic downgrade, external cause separation, serial non-overlap, cross-investigation concurrency, transient AI retry classification, scoped lease recovery, idempotent resume, manual retry lineage, archive immutability, operation detail, SSE replay, audit pagination, permissions, masking, V1-to-head migration, and fresh-schema creation.

Before declaring work complete, assess architecture, dependency, and development-workflow impact. Update this file immediately when any of those contracts change, then verify the documented commands and behavior match the implementation.
