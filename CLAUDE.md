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
- Any lifecycle/schema change requires a new incremental Alembic revision, never
  edits to the existing baseline migration. Update this file and README whenever
  architecture, dependencies, or the development workflow changes.

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
