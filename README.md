# Lode

Lode turns production incident alerts into evidence-backed incident causes and
exact code diagnoses. It accepts the strict `incident.alert.v1` Kafka contract
or authorized manual input, executes bounded read-only investigations, archives
the complete evidence trail, and refuses to present a related source file as a
confirmed defect without runtime-authoritative proof.

The project is unreleased. It maintains one current architecture, one API, and
one database baseline. There are no compatibility routes, schema adapters,
dual writes, or historical payload converters.

## Investigation Model

Each investigation freezes its Workspace control state at intake: repository
bindings, resource graph, Connector scopes and health, model bindings and
policy, the selected investigation-depth policy revision, output language, and
immutable normalized input. The worker then runs
serial decision waves. Independent operations inside one wave may run in
parallel, with a hard maximum of four.

The server owns capability selection, query authorization, budgets,
counter-evidence requirements, and stopping rules. Model output can propose only
strict typed candidates. It cannot directly execute SQL, LogQL, JSON queries,
URLs, commands, paths, or credentials.

Result states are:

- `pending`: no terminal report exists.
- `confirmed`: an incident-revision code finding passed structural authority
  gates and independent semantic verification.
- `hypothesis`: one supported mechanism remains to be validated.
- `insufficient`: available evidence cannot support one cause.
- `unavailable`: a required capability or strict output contract failed.

Reports always separate `incident_cause` from `code_diagnosis`. External
dependency, network, data, and infrastructure failures can be accurate incident
causes while code diagnosis independently reports `no_defect`, `not_found`, or
an exact resilience weakness.

## Source Authority

A code finding references an immutable source artifact with repository ID, full
SHA, repository role, path, symbol, and exact line bounds. `confirmed` requires
the runtime-observed incident SHA and a stack, runtime, dependency, or archived
alert link to the location. A missing, ambiguous, or unresolvable revision is an
explicit evidence gap. The default branch never substitutes for incident code.

Repository search in other bound repositories produces candidates only. A
candidate must independently establish its runtime revision and relationship to
the incident before it can contribute to a confirmed conclusion. Configuration
without runtime evidence cannot prove deployed behavior.

## Workspaces And Models

A Workspace owns one globally unique Kafka ingestion topic. Starting or
resuming ingestion is allowed only when:

1. the topic is configured;
2. the active model policy covers planner, native query, synthesizer, verifier,
   and context compactor roles with healthy deployments;
3. the broker confirms the topic is reachable.

Repository, ResourceGraph, and evidence capability gaps remain visible but do
not block intake.

The sole system administrator manages encrypted AI provider credentials,
Workspace configuration, users, and Workspace grants. Ordinary users enter
only the Workbench and receive either `viewer` (read-only) or `operator`
(manual investigation and retry) permission per Workspace. The administrator
has unrestricted Workbench and investigation access. Routing chooses a model
per role and execution class, applies per-binding token/call/cost limits, and
records every selection, context bundle, invocation, usage result, and failure.
Provider switches never carry hidden reasoning state.

## Evidence Access

Workspace-owned Connectors support Loki, Elasticsearch, OpenSearch,
PostgreSQL, MySQL, cataloged HTTPS reads, and an isolated command runner. Native
candidate parsers and policies enforce scope, time, row, byte, cardinality,
and cost limits before issuing a single-use signed authorization.

PostgreSQL/MySQL adapters require read-only replicas and restricted roles. HTTPS
uses canonical, redirect-free, byte-bounded transport. The command
runner is a separate process and image with its own key, private network,
read-only filesystem, fixed executables, empty child environment, resource
limits, syscall policy, replay protection, and a current Connector lifecycle
check before each new command execution.

All provider, Connector, and Git secrets are encrypted with a data-encryption
key derived from `LODE_MASTER_KEY`, stored separately from ordinary config, and
never returned. Evidence-read authorization and Runner signing use distinct keys.

## Kafka Contract

Kafka input uses `incident.alert.v1`. Unknown fields are rejected. The topic,
not the payload, selects the Workspace. `trace_id` is an opaque optional string
and is preserved exactly in encrypted storage; source revision is a required
full lowercase Git SHA used only for source resolution.

```json
{
  "schema_version": "incident.alert.v1",
  "alert_id": "alert-01",
  "occurred_at": "2026-08-27T10:38:59.522Z",
  "severity": "CRITICAL",
  "event": "payment.order_create.failed",
  "trace_id": "opaque producer value",
  "source_revision": "6c36658895cb220b66f89f17718a001f3f9f02e4",
  "error": {
    "type": "GatewayError",
    "message": "Payment creation failed",
    "stack": "GatewayError: Payment creation failed\n    at createOrder (src/order.ts:42:7)",
    "cause": null
  }
}
```

Kafka and manual intake use the same normalization, masking, immutable evidence,
control snapshot, idempotency, and durable job creation path.

## API And Web

The final FastAPI surface includes:

- global provider accounts and model deployments;
- Workspace lifecycle, model bindings/policy, repositories, ResourceGraph
  views, Connector instances, verification, and introspection;
- manual investigation creation, searchable/paginated list, human-readable
  canonical detail, explicit technical detail, paginated masked audit, SSE,
  retry, and archive;
- authentication, users, invitations, health, and metrics.

`GET /investigations/{id}` is the canonical client state. SSE replays persisted
operation events by sequence, accepts `Last-Event-ID`, emits
`investigation.finished` for terminal state, and is used only to trigger a
canonical reload.

The Next.js Web app provides global model and AI-output-language settings,
Workspace settings including a three-profile investigation-depth selector,
manual intake, a searchable investigation list, and a responsive investigation
detail that leads with the incident summary, cause, diagnosis, evidence, and
next action. Technical snapshots remain available only in an explicit technical
view. Wide tables and tab lists scroll locally instead of widening the page.

## Local Development

The master key must contain at least 32 bytes. It derives independent JWT,
data-encryption, and evidence-authorization keys; the Runner key remains
isolated from the application key material:

```bash
export LODE_MASTER_KEY='replace-with-a-random-secret-at-least-32-bytes'
export LODE_COMMAND_RUNNER_KEY='replace-with-a-runner-only-secret-at-least-32-bytes'
cp docker-compose.example.yml docker-compose.yml
docker compose up --build
```

`docker-compose.yml` is intentionally ignored because it is the local Compose
override. Keep local ports, credentials, and deployment-specific settings there;
use `docker-compose.example.yml` as the committed template.

Web is available at `http://localhost:3000`; the API is available at
`http://localhost:8000`. Seed only fresh development databases:

```bash
uv sync --all-extras
uv run alembic upgrade head
uv run python scripts/seed.py
```

Run processes individually with:

```bash
make serve
make consume
make work
npm run dev --prefix apps/web
```

## Verification

The complete deterministic local gate, run once against a fresh isolated upgraded
PostgreSQL database, is:

```bash
make local-release-check
```

Its constituent targets are:

```bash
make contracts
make schema-check
make intake-check
make resource-check
make evidence-access-check
make log-connectors-check
make native-connectors-check
make investigation-check
make analysis-check
make api-check
make hardening-check
make web-check
uv run python -m compileall -q src scripts alembic tests
uv run pytest -q
uv run python scripts/check_forbidden_contracts.py
```

Schema verification must use a fresh PostgreSQL database and run both
`alembic upgrade head` and `alembic check`. The only migration is
`alembic/versions/0001_initial.py`.

`make analysis-check` is a deterministic smoke gate. A release additionally
requires frozen real-provider observations to pass the quality thresholds and
Wilson confidence bounds documented in `CLAUDE.md`; the deterministic corpus is
intentionally too small to claim that statistical release gate.

```bash
make provider-release-check \
  PROVIDER_OBSERVATIONS=/absolute/path/provider-observations.jsonl \
  PROVIDER_RUN_MANIFEST=/absolute/path/provider-run-manifest.json \
  OPERATIONAL_OBSERVATIONS=/absolute/path/operational-observations.jsonl \
  OPERATIONAL_BASELINE=/absolute/path/operational-baseline.json \
  CANARY_BASELINE_OBSERVATIONS=/absolute/path/canary-baseline-observations.jsonl \
  CANARY_BASELINE_RUN_MANIFEST=/absolute/path/canary-baseline-run-manifest.json \
  RELEASE_BUNDLE=/absolute/path/release-bundle.json
```

The release bundle binds the SHA-256 of every supplied artifact plus the frozen
gold and complete evaluation corpora. Candidate and baseline are distinct runs
and differ in at least one frozen model, prompt, schema, or policy revision. The
baseline itself must pass the statistical gate; any aggregate or per-case
canary regression blocks release.
