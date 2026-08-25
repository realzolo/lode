# Lode

Lode turns production alerts into evidence-backed incident causes and exact code diagnoses. It accepts strict Kafka `alert.v1` messages or authorized manual input, executes one investigation action at a time, archives every result, and refuses to label a related file as a proven defect.

## Investigation Contract

Every investigation follows this sequence:

1. Normalize and redact the complete incident input.
2. Parse the error contract and stack frames.
3. Form a leading mechanism and choose one server-registered read-only action.
4. Archive the action input, progress, result, duration, failure, and evidence links.
5. Update supporting facts, counter-evidence, and missing validation.
6. Produce separate `incident_cause` and `code_diagnosis` results.

Actions within one investigation never overlap. Workers may process different investigations concurrently. Each investigation is bounded to 12 evidence actions, 10 model calls, and 10 minutes. Repeating an action fingerprint is rejected, and durable steps allow a worker to resume from the first unfinished action.

### Result States

- `pending`: no terminal result exists yet.
- `confirmed`: an incident-version code finding passed structural and independent semantic verification.
- `hypothesis`: one leading mechanism has supporting evidence but still needs validation.
- `insufficient`: the available evidence cannot support a single cause.
- `unavailable`: a required analysis capability was unavailable, or both strict structured-output attempts failed validation. Output-contract failure is explicitly distinguished from insufficient evidence.

Lode does not expose a model-generated confidence score. Non-confirmed reports contain evidence requests and test suggestions, not production code change instructions.

### Code Findings

A source file is only a candidate. A code finding must identify an immutable artifact with repository ID, full revision SHA, revision role, path, symbol, and an exact line range. It also records:

- faulty behavior, explicit contract violation, and expected behavior;
- trigger condition and propagation from the code branch to the observed error;
- incident, supporting, and counter-evidence references;
- missing validation, a minimal fix direction, and a verification test.

`confirmed` additionally requires an immutable incident baseline plus a stack, runtime, dependency, or alert-contract link to the incident. Lode resolves the baseline independently for every bound repository: it first tries the alert deployment revision, then freezes that repository's default branch HEAD if the revision does not belong to that repository. With no deployment revision, the default branch is used directly. The selected basis is recorded in operation and evidence metadata. An independent model pass must still verify the branch, trigger, and propagation. Documentation and lexical matches can provide context but cannot prove a defect.

External failures remain valid incident causes. Code diagnosis independently reports `no_defect`, `not_found`, or an exact resilience finding such as missing timeout, retry, validation, or error preservation.

## Application Startup

An application can start or resume Kafka ingestion only when it has at least one bound repository, a configured Kafka topic, and an explicitly selected AI model that passed a live protocol test. Binding a model tests it before saving; model settings also provide an explicit retest action. The backend checks these requirements in one fail-closed gate and returns HTTP 409 with `error.code = "application_not_ready"` plus `error.details.missing` when configuration is incomplete. A global default model does not replace the required application model selection.

The application dashboard shows the three startup requirements before activation and disables submission while its current application snapshot is incomplete. The API repeats the checks on every start and resume, so stale UI state and direct API calls cannot bypass them.

## Kafka `alert.v1`

Messages are strict and reject unknown top-level fields. `version` and `git_commit` are top-level deployment fields. The complete `error_log.stack`, recursive `cause`, `properties`, business `fields`, trace context, version, revision, and time window are normalized and archived after secret masking.

```json
{
  "schema_version": "alert.v1",
  "alert_id": "PB_SlZBH_Wt",
  "occurred_at": "2026-08-25T10:38:59.522Z",
  "event_type": "payment.order_create.gateway_failed",
  "level": "CRITICAL",
  "title": "Payment order creation failed",
  "dedupe_key": "alert:payment.order_create.gateway_failed:sha1",
  "dedupe_ttl_seconds": 300,
  "version": "1.1.21",
  "git_commit": "6c36658895cb220b66f89f17718a001f3f9f02e4",
  "fields": {
    "providerCode": "Payssion",
    "methodCode": "enets_sg",
    "gatewayCode": "PAYMENT_FAILED"
  },
  "error_log": {
    "name": "object",
    "message": "{\"success\":false,\"code\":\"PAYMENT_FAILED\",\"message\":\"Payment creation failed\"}",
    "stack": null,
    "properties": {
      "value": {
        "success": false,
        "code": "PAYMENT_FAILED",
        "message": "Payment creation failed"
      }
    },
    "cause": null
  }
}
```

When an error is serialized as an object, Lode promotes the structured `properties.value` contract and JSON message into searchable error code and message fields while retaining the original wire value. Source lookup prioritizes stack locations, incident-revision symbols, error contract identifiers, related symbols, then default-branch reference material.

## API

- `POST /investigations`: authorized manual intake with application, error message, stack, occurrence time, deployment version, trace, structured fields, and bounded redacted attachments.
- `GET /investigations`: investigation list.
- `GET /investigations/{id}`: normalized input, report, ordered steps, decisions, operations, evidence, and code findings.
- `GET /investigations/{id}/events`: durable operation event history.
- `GET /investigations/{id}/audit`: separately paginated operation and AI-call audit streams.
- `GET /investigations/{id}/stream`: live SSE updates.
- `POST /investigations/{id}/retry`: create a new run from a terminal run's immutable input after a live model test.
- `POST /investigations/{id}/archive`: permanently make a terminal run read-only.

SSE event names are `step.updated`, `operation.started`, `operation.progress`, `operation.finished`, `decision.recorded`, `code_finding.updated`, `report.updated`, and `investigation.finished`.

Kafka and manual intake call the same normalization, evidence archiving, and job creation service.

## Data Model

`alembic/versions/0001_initial.py` is the frozen V1 baseline. `0002_model_health_retry_archive.py` upgrades an existing V1 database with model health results, AI retry attempt audit, investigation retry lineage, and irreversible archive state.

There is no historical payload conversion, dual schema, or API adapter. Partial unique indexes enforce at most one running step and one running operation per investigation. Server startup applies pending migrations automatically.

```bash
uv sync --extra dev
export LODE_SECRET_KEY='replace-with-a-random-secret'
uv run alembic upgrade head
uv run python scripts/seed.py
```

The Web app uses pnpm:

```bash
cd apps/web
pnpm install
pnpm dev
```

For the complete local stack:

```bash
export LODE_SECRET_KEY='replace-with-a-random-secret'
docker compose up --build
```

Web is available at `http://localhost:3000`; the API is at `http://localhost:8000`.

## Development

Run the backend services individually:

```bash
uv run uvicorn lode.api.main:app --reload
uv run python -m lode.consumer.main
uv run python -m lode.worker.main
```

Verification:

```bash
uv run pytest -q
uv run python -m compileall -q src scripts alembic tests
cd apps/web && pnpm typecheck && pnpm build
```

Schema verification must run `alembic upgrade head` against a newly created PostgreSQL database. Source evidence collection needs read access to the repositories configured for the application. All connector inputs are server-controlled and read-only; model output cannot introduce commands, SQL, URLs, paths, or credentials.

OpenAI-compatible base URLs are resolved to `/v1/chat/completions`, and Anthropic base URLs to `/v1/messages`. Evidence-rich model calls use provider-enforced strict JSON Schemas, an 8192-token output bound, and a configurable 120-second per-attempt timeout; health probes use 30 seconds. OpenAI-compatible providers use JSON Schema response format and Anthropic uses a forced schema-bound tool result. Transient failures (network, timeout, HTTP 429, and HTTP 5xx) retry with visible progress, while deterministic authentication, request, or non-JSON protocol failures stop immediately. The report and AI audit show the precise failure and actual attempt count instead of a generic unavailable label. Two invalid structured outputs make that run unavailable and explicitly do not imply that its evidence was insufficient.

The workbench only renders code attached to final validated code findings. Intermediate source candidates remain audit metadata, the Monaco viewer follows the dashboard theme, and stable skeletons plus non-blocking SSE reconnect state prevent full-page flicker during live updates.
