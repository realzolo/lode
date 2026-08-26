# Lode Project Context

Lode is an evidence-backed production incident investigation service. A V1 investigation advances through serial decision waves; independent operations inside one wave may run concurrently with a hard limit of four. There are no historical protocol or execution-path adapters.

## Runtime Components

- `src/lode/api`: FastAPI control plane, authorization, manual intake, investigation detail, audit pagination, and SSE.
- `src/lode/consumer`: strict Kafka `incident.alert.v1` validation and shared intake dispatch.
- `src/lode/worker`: durable job claiming, investigation-scoped leases, retry, and bounded cross-investigation concurrency.
- `src/lode/engine/investigation_intake.py`: canonical normalization, masking, immutable input evidence, and job creation.
- `src/lode/engine/investigation_engine.py`: one-action-at-a-time adaptive decision loop and terminal synthesis.
- `src/lode/engine/structured_outputs.py`: provider-neutral strict JSON Schemas for decisions, reports, and causal verification.
- `src/lode/integration_policy.py`: extensible integration-kind registry, config/secret validation, capabilities, UI form metadata, and egress policy.
- `src/lode/engine/integrations.py`: provider adapters for verification and bounded snapshots.
- `src/lode/engine/log_integrations.py`: `log_search` provider-adapter registry; the investigation core never requires a specific log product.
- `src/lode/engine/loki_investigation.py`: built-in bounded Loki adapter for request/lifecycle discovery within an immutable service scope.
- `src/lode/engine/investigation_evidence.py`: bounded Git and unified integration evidence collection.
- `src/lode/engine/evidence/git.py`: stack parsing, exact revision lookup, symbol range extraction, lexical candidates, and related-symbol expansion.
- `src/lode/engine/investigation_events.py`: durable step, operation, progress, failure, timing, and evidence events.
- `apps/web`: Next.js workbench with manual intake, wave/operation execution track, final-finding code viewer, and paginated audit drawer.

PostgreSQL is the source of truth. Kafka consumers only validate and enqueue; workers execute investigations. Model configuration is selected from the application binding first, then the global default. The global default does not satisfy the application activation requirement described below.

## Application Activation

Every transition into active Kafka ingestion fails closed unless the application has all three required bindings:

1. exactly one active primary `ApplicationServiceBinding` whose service maps to a global repository;
2. the application's required, globally unique `Application.ingestion_topic`;
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

`GET /applications` exposes `service_count` and `primary_service_configured`; it does not use repository count as a runtime-readiness proxy. The Web start dialog displays all requirements and cannot submit while its current application snapshot is incomplete. Binding a model performs a live protocol probe before persisting the binding. `POST /settings/ai-models/{id}/test` repeats the probe on demand. Editing endpoint, key, provider, or model resets health to `untested`. The backend gate remains authoritative for stale clients and direct API callers.

## Investigation Execution

An investigation owns exactly one active decision wave:

1. Canonicalize and mask the complete error input.
2. Parse stack frames and the structured error contract.
3. Build or update the leading mechanism.
4. Select one action ID from the server-generated catalog.
5. Execute that action as one wave. A log-search or Git wave may launch up to four independent operations concurrently.
6. Persist every operation result and evidence artifact independently; one failed operation does not cancel its siblings.
7. Start the next decision only from evidence committed by the prior wave.
8. Archive evidence, facts, counter-evidence, and validation gaps.
9. Synthesize separate incident and code results and persist one terminal report.

Parallelism is allowed only inside an explicit wave and must remain at or below four operations. Allocate operation IDs/ordinals before launching work, use `return_exceptions=True`, and persist each result separately. Do not overlap decision waves. `engine_concurrency` separately controls how many investigations workers may run.

Limits are configured by:

- `investigation_max_evidence_steps`, default 12;
- `investigation_max_model_calls`, default 10;
- `investigation_timeout_seconds`, default 600.

Every action uses a server-generated fingerprint and may run once. Connector evidence reuse uses the complete immutable query fingerprint, including connector, endpoint, generated query, time window, direction and limit. Steps and operations commit independently. PostgreSQL permits one running step/decision wave while multiple operations in that wave may run. On worker recovery, completed evidence is reused and the first unfinished durable action resumes. Lease cleanup only changes jobs whose own leases expired.

Model output may select catalog IDs and cite archived evidence IDs. It may never create LogQL, SQL, commands, service names, URLs, time windows, repository paths, connector configuration, or credentials.

OpenAI-compatible base URLs are normalized to `/v1/chat/completions`; Anthropic base URLs are normalized to `/v1/messages`. Investigation requests use a configurable 120-second per-attempt timeout, provider-enforced strict JSON Schemas, and an 8192-token output bound; health probes keep a separate 30-second timeout. OpenAI-compatible providers use `response_format.json_schema`, while Anthropic uses a forced schema-bound tool result. Calls automatically retry only transient network failures, timeouts, HTTP 429, and HTTP 5xx with bounded exponential backoff. Authentication, request validation, and non-JSON protocol responses fail immediately. Each retry emits operation progress, and AI audit rows retain the actionable error classification and actual attempt count. Do not collapse a timeout or protocol error into a generic "model unavailable" message. If both the initial structured response and the single repair fail validation, report the analysis as unavailable with the exact contract error; never relabel output-format failure as insufficient evidence.

## Input Contract

Kafka accepts only strict `incident.alert.v1` with no compatibility aliases. Required fields are `schema_version`, `alert_id`, `occurred_at`, `severity`, stable `event`, `service_name`, `environment`, full 40-character `git_commit`, UUID v4 `request_id`, `correlation`, and structured `error` (`type`, `message`, `stack`, recursive `cause`). The Kafka topic selects the Lode application; no business application identifier is accepted from the payload.

`request_id` is the sole cross-service correlation key. Lode does not accept `trace_id`, `traceparent`, baggage, a service-name request header, or a carrier wrapper. A real distributed-tracing contract may only be introduced later together with spans and a tracing backend.

Manual `POST /investigations` requires an explicitly bound source service and bounded error input. Environment, UUID v4 request ID and incident commit are optional operator evidence; omitting them disables the corresponding log-search/source action and must remain an explicit evidence gap. It requires application `analyze` permission and calls the same normalization service as Kafka. Unbound source services fail closed.

Complete error input is masked and archived without discarding stack or cause information.

## Source Investigation

`Service`, source repository, and Lode `Application` are separate identities. `Service.service_name` is globally unique and maps to one global `GitRepo`; `ApplicationServiceBinding` is many-to-many with one `primary` and any number of `shared` services. Investigation creation copies these bindings into immutable `InvestigationServiceSnapshot` rows, and all Loki/Git access is limited to that snapshot. A discovered unbound `peer_service` is recorded as an evidence gap and never queried.

The current runtime service identities to register are `pornbox`, `payment-gateway`, and `sonakit`. They must exactly match each deployment's own `SERVICE_NAME`; service identity is never learned from an inbound request header.

All external services are application-owned `ApplicationIntegration` rows. Database, Kafka, ClickHouse, Loki, and Prometheus are peers, and every kind may have multiple named instances per application. `kind` is unconstrained text in PostgreSQL; runtime support comes from the code registry, which owns its version, config schema, secret contract, capabilities, form metadata, verification adapter, and evidence adapter. Adding a kind does not require a schema migration.

Built-in capabilities are `test`, `snapshot`, `query_catalog`, and `log_search`. The engine selects behavior by capability, never by product name. Loki is the built-in `log_search` adapter, not a required dependency; another log product can replace it by registering a kind and log adapter. Prometheus is a snapshot integration. Redis is not supported.

Database integrations support PostgreSQL and MySQL through structured DNS host, port, database, username, mandatory TLS, encrypted password, qualified allowed-table catalog, and optional sensitive-column masks. AI never supplies SQL or connector queries. Only the server-owned `sample` and `count` operations are accepted. Kafka evidence integrations are independent of `Application.ingestion_topic` and are limited to administrator-allowlisted topics and consumer groups.

All user-managed secrets are submitted as values, encrypted immediately with `LODE_DATA_ENCRYPTION_KEY`, stored separately from non-secret config, and never returned. Indirect environment-reference syntax is prohibited for integrations, AI keys, and Git credentials. The JWT signing key is never reused for encryption. Every integration endpoint must pass the application process egress allowlist and the deployment network policy must enforce the same boundary.

Application architecture context is configured beside the application's model selection. At investigation creation, the current ordered context entries are masked and frozen as an immutable `application_context` evidence artifact. Every model phase receives that snapshot as explicitly untrusted background: it may clarify service boundaries and architecture, but it cannot override system rules or independently prove an incident cause or code defect. Later context edits affect only new investigations.

Loki queries are generated only by server helpers. Streams are selected by low-cardinality `service_name` and `environment`, then JSON is filtered by UUID v4 `request_id`. When the initial request evidence only covers one async stage, the engine may query allowlisted `order_id`, `job_id`, `delivery_id`, or `provider_transaction_id` lifecycle keys discovered from committed evidence. Request IDs and business IDs must never be Loki labels.

Source lookup order is fixed:

1. Exact file, line, class, and function from the stack at the incident revision.
2. Incident-revision symbols and structured error identifiers.
3. Error code definitions and references.
4. Caller, callee, error branch, exception conversion, return checks, timeout, retry, and related tests.
5. Resolve each participating service only at the full `git_commit` observed in its own runtime evidence. Multiple commits for one service remain separate incident revisions.

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

For `confirmed`, the revision must be the immutable runtime-observed incident commit and the location must be linked by stack, runtime/dependency evidence, or the archived alert contract. Missing, malformed, or unresolvable commit evidence is an explicit evidence gap; the default branch must never stand in for incident code. The independent verifier must still prove the branch, trigger, and propagation.

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

The project has not released its database baseline. `alembic/versions/0001_initial.py` is the single complete V1 schema, including model health, retry/archive state, service directory/bindings/snapshots, request IDs, and Loki evidence support. There is no V2 migration or old-schema adapter; development databases are recreated from V1. Freeze V1 only after the first release, then use forward migrations.

The investigation-owned tables are inputs, service snapshots, steps, decisions, operations, operation events, evidence collections/artifacts/links, source revisions, findings/edges, code findings, AI invocations, reports, and jobs. Evidence content is immutable after archival. Application integrations are the single external-service ownership model. Their non-secret JSON, encrypted secret JSON, kind version, instance revision, verification status, and collection health are persisted separately.

Until V1 is released, model changes are folded into `0001_initial.py`. Verify a fresh schema:

```bash
LODE_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/lode_migration_test \
LODE_SECRET_KEY=test-secret \
LODE_DATA_ENCRYPTION_KEY=test-data-encryption-key \
uv run alembic upgrade head
uv run alembic check
```

Alembic autogeneration against that database must produce no schema difference.

## Frontend Contract

Application creation atomically requires both name and Kafka ingestion topic; the topic cannot be cleared later. The application settings navigation exposes one `Integrations` page rather than a top-level database page. It renders a dense, filterable instance list and builds create/edit forms from `GET /integration-kinds`, so a new kind does not require a frontend conditional. Each row exposes kind, capabilities, revision, verification/state, and instance operations without showing secrets. Application admins, not only global admins, may manage their application's integrations.

Model selection and architecture context share one `Model & Context` page. There is no standalone descriptions route or navigation item. Application admins manage both controls in this workspace; context is presented as model background rather than as a peer application resource.

The investigation workbench displays the incident cause first and code diagnosis second. Only source artifacts referenced by final validated code findings may appear in the main code viewer; lexical, context, and intermediate source candidates remain available only as masked audit references. The code viewer is read-only, follows the current application theme, and highlights the exact finding range alongside where it is wrong, why, trigger, propagation, expected behavior, missing validation, and test.

Execution uses a vertical decision-wave track and shows sibling operations within each wave. Every operation exposes its purpose, masked input, progress events, actual result, duration, failure, metrics, and evidence links. Running states use restrained motion with a reduced-motion fallback. Initial data and Monaco loading surfaces reserve stable dimensions, and transient SSE refresh failures preserve the last canonical result instead of replacing the page. Waiting and running states have stable dimensions on desktop, tablet, and mobile.

Do not restore a second investigation UI or translate historical response shapes.

## Development And Verification

Backend:

```bash
uv sync --extra dev
export LODE_DATA_ENCRYPTION_KEY='replace-with-an-independent-random-secret'
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

For investigation changes, tests must cover strict `incident.alert.v1` parsing, UUID v4 request validation, bound-service access control, generated LogQL, full query fingerprints, lifecycle-key allowlists, bounded wave concurrency and partial failure, runtime-commit-only source lookup, full error preservation, exact stack/source range, candidate rejection, incident revision gates, strict model response schemas, independent semantic downgrade, external cause separation, cross-investigation concurrency, transient AI retry classification, scoped lease recovery, idempotent resume, manual retry lineage, archive immutability, operation detail, SSE replay, audit pagination, permissions, masking, and fresh V1 schema creation.

Before declaring work complete, assess architecture, dependency, and development-workflow impact. Update this file immediately when any of those contracts change, then verify the documented commands and behavior match the implementation.
