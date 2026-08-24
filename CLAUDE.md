# Lode Development Context

## Application ingestion lifecycle

- `applications.ingestion_state` is the desired control-plane state:
  `draft`, `active`, or `paused`. New applications start as `draft`; existing
  topic bindings are migrated to `paused` for explicit operator confirmation.
- Only `active` applications are Kafka subscription targets. The consumer polls
  the database for exact active topics, so no global topic regex is a second
  source of truth and no service restart is needed after a state change.
- First start is explicit and validates the Kafka topic's partition metadata.
  Administrators choose `latest` (new messages only) or `earliest` (Kafka
  retention replay). Chosen per-partition offsets are persisted before Kafka
  commits so activation retries do not change the selected starting point.
- `paused` stops new intake and worker claims. In-flight analyses finish; queued
  jobs and Kafka offsets remain for resume. Runtime observation (`starting`,
  `listening`, `error`) is separate from desired state and is consumer-owned.
- The application list is the lifecycle control surface: an application admin
  starts a draft or migrated paused application there, choosing `latest` or
  `earliest`; active and paused cards expose Pause and Resume respectively.
  The application detail page is configuration-only, including Kafka topic edits.

## Operations

- Run the API, consumer and worker independently in local development:
  `make serve`, `make consume`, and `make work`.
- Analysis intake and execution are deliberately separate. The consumer commits
  the alert and a completed `receive` workflow step together with a durable
  `queued` job. The worker claims that job and begins at `git_sync`; if no
  worker is running, the detail API reports the real job queue state rather
  than showing alert receipt as pending.
- Alert summaries use the strict producer `error_log.message` when present,
  then the controlled `fields.error`, `fields.reason`, `fields.message`, or
  `fields.detail` fallback order. Internal workflow node identifiers are never
  presentation copy; the web pipeline owns their localized labels.
- `analyses.public_id` is the opaque public identity of one analysis run and
  is the only identifier accepted by `/analyses/{analysis_id}` and the web
  route. `dedupe_key` remains an internal incident-correlation value and must
  not be placed in URLs or used to select the “latest” analysis.
- Docker Compose passes one `LODE_KAFKA_*` configuration set to all backend
  services. Its local Kafka advertises an internal `kafka:9092` listener and a
  host `localhost:9092` listener. Consumer and worker wait for the API health
  check, which runs only after API-owned migrations complete.
- The project is in its fresh-initialization phase and has one self-contained
  Alembic baseline, `0001_initial`. Delete and recreate a local database before
  applying it. After the first production deployment, schema changes must use a
  new incremental revision rather than editing this baseline. Update this file
  and README whenever architecture, dependencies, or the development workflow changes.
- The database uses PostgreSQL's default `public` search path. ORM metadata and
  V1 DDL intentionally leave table names unqualified so Alembic drift checks
  compare the same identities.

## Analysis Configuration And Isolation

- `platform_settings.ai_output_language` is the global, persisted language for
  human-readable AI analysis output. It supports the same locales as the web UI
  (`en` and `zh`), defaults to `en` when unset, and is changed only through the
  admin `PUT /settings/ai-output-language` endpoint. The runner resolves it for
  each new analysis; completed analyses remain immutable.
- Git evidence collection creates a unique temporary sandbox for every analysis
  below `LODE_EVIDENCE_GIT_CACHE_DIR`. Repository clones are never shared across
  analysis tasks and are deleted after masked evidence excerpts are persisted.
  The default is `/tmp/lode/git`; ensure an overridden directory is writable by
  the worker. If it is unavailable, analysis degrades without Git evidence
  rather than failing the whole task.

## Read-Only External Integrations And Evidence Time Scope

- Applications can bind Redis, Kafka, and ClickHouse integrations. Configuration
  is a strict, service-specific non-secret selector; credentials are encrypted at
  rest and never returned by the API. Application readers receive only status;
  global admins use the dedicated configuration endpoint. Collectors require TLS,
  DNS endpoints, and a non-empty `LODE_INTEGRATION_EGRESS_ALLOWLIST`; deployment
  egress policy must enforce the same destinations.
- The worker owns all external calls. The LLM never receives a DSN, credential,
  connector, shell, executable SQL interface, alert payload, or deployment text.
  It receives only bounded, redacted, immutable evidence excerpts. Conclusions,
  facts, and inferences require valid artifact references; otherwise the result is
  an explicit low-confidence evidence-insufficient hypothesis.
- PostgreSQL data sources and ClickHouse bindings must prove that their effective
  account has no write or temporary-object capability before they are saved and
  before every catalog query. PostgreSQL accepts only schema-qualified base tables
  and exposes the server-owned `sample`/`count` query catalog, never SQL text.
  PostgreSQL bindings are either encrypted structured credentials with
  `sslmode=verify-full`, or an `env://NAME` reference whose resolved DSN also has
  `sslmode=verify-full`; plaintext DSNs and TLS downgrade modes are rejected by
  both API validation and the database constraint, with no runtime bypass.
  Redis and Kafka may use operational credentials with write grants: their typed
  collectors expose fixed status reads only and no arbitrary-command/write API.
  ClickHouse grants are an allowlist of SELECT access to configured and fixed
  system relations. Policy failures disable the binding and audit it; availability
  failures yield partial evidence only.
- `service_snapshot` runs after `context` and before `ai_analysis`, using fixed
  read-only status templates only. Alert, deploy, Git, database, service, and
  operator-guidance artifacts are content-hashed and immutable. Snapshots record observation start/
  end, temporal scope, config hash, collector version, source position, redacted
  excerpt, and verification metadata. They are observations at analysis time, not
  proof of state at the earlier alert time.
- The complete analysis path is `receive -> git_sync -> context -> service_snapshot
  -> experience -> ai_analysis -> conclusion`. Experience retrieval is a low-trust
  historical reference: it is injected into the AI prompt for comparison, but it
  is never a citable evidence artifact and must be verified against current evidence.
- Non-core evidence collectors fail independently. A degraded collector records its
  reason on the workflow step and the analysis continues with available evidence.
  Such runs use the persisted `needs_review` analysis status (the execution job can
  still be `succeeded`) and must not be promoted to reusable experience when the
  evidence is insufficient.
- Completed runs persist a structured advisory remediation record and a canonical,
  Markdown Agent prompt assembled only from redacted evidence. The platform never
  executes generated commands or changes. Users with `analyze` permission can submit
  per-target useful/not-useful feedback through
  `POST /analyses/{analysis_id}/feedback`; feedback is append-audited and idempotent
  per user, analysis, and target.
- Analysis recommendations and feedback are in the `0001_initial` baseline. A
  recommendation declares whether it is `evidence_backed` or a
  `safety_fallback`; fallback advice is never presented as verified remediation.
  Runs with warnings, unresolved unknowns, insufficient citation coverage,
  fallback remediation, heuristic output, or low confidence are `needs_review`.
  No new runtime dependency is required. The default Agent prompt size is capped
  at 16,000 characters and all interpolated content is redacted again at export.
- `redis` and `clickhouse-connect` are runtime dependencies.
