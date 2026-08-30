# Lode

Lode turns production incident alerts into evidence-backed incident causes and
exact code diagnoses. It accepts the strict `incident.alert.v2` Kafka contract
or authorized manual input, executes bounded read-only investigations, retains
the complete immutable evidence trail, and refuses to present a related source file as a
confirmed defect without repository-scoped source authority. Independently
verified runtime evidence may confirm an external service, configuration,
network, or data cause without a code finding.

The project is unreleased. It maintains one current architecture, one API, and
one database baseline. There are no compatibility routes, schema adapters,
dual writes, or historical payload converters.

## Investigation Model

Each immutable investigation run freezes its Workspace control state at intake: repository
bindings, resource graph, Connector scopes and health, model bindings and
policy, its code-owned execution ceiling and time window, output language, and
immutable normalized input. The worker then runs
serial decision waves. Independent operations inside one wave may run in
parallel, with a hard maximum of four.

The server owns capability selection, query authorization, budgets,
counter-evidence requirements, and stopping rules. Model output can propose only
strict typed candidates. It cannot directly execute SQL, LogQL, JSON queries,
URLs, commands, paths, or credentials.

Result states are:

- `pending`: no terminal report exists.
- `confirmed`: an evidence-backed incident cause or authoritative code finding
  passed independent semantic verification.
- `hypothesis`: one supported mechanism remains to be validated.
- `insufficient`: available evidence cannot support one cause.
- `unavailable`: a required capability or strict output contract failed.

Reports always separate `incident_cause` from `code_diagnosis`. External
dependency, network, data, and infrastructure failures can be accurate incident
causes while code diagnosis independently reports `no_defect`, `not_found`, or
an exact resilience weakness.

## Source Authority

A binding has two orthogonal fields: `analysis_mode` (`code` or
`documentation`) and `is_alert_source`. Every Kafka-enabled Workspace has
exactly one active code binding marked as the alert source. Build Units,
Components, and identity aliases express service, gateway, Worker, library, and
infrastructure semantics; bindings do not use repository roles.

Kafka `source_revision` is authoritative only for the alert-source repository
and is frozen without a remote Git availability check. The first source read of
any other code repository resolves and freezes its bound branch HEAD, which the
Workspace contract treats as that repository's authoritative production
revision. Only an explicit runtime SHA conflict or an evidence-grounded exact
path/symbol incompatibility creates `source_snapshot_incompatible`. Generic
absence of deployment-version proof is not an evidence gap.

A code finding references a source artifact with repository ID, full SHA,
revision origin, path, symbol, and exact line bounds. Source authority gates
only findings for that repository. Reports include only repositories actually
read or identified by runtime evidence; unrelated bindings do not generate
assessments or gaps. Configuration claims still require runtime evidence.

## Workspaces And Models

A Workspace owns one globally unique Kafka ingestion topic. Starting or
resuming ingestion is allowed only when:

1. the topic is configured;
2. the active model policy covers planner, native query, synthesizer, verifier,
   and context compactor roles with healthy deployments;
3. the broker confirms the topic is reachable;
4. exactly one active code repository is marked as the alert source.

Stale repository analysis and evidence capability gaps remain visible as
warnings; a missing or ambiguous alert-source binding blocks Kafka activation.

The topic can change while ingestion is draft or paused. A real change resets
ingestion to draft, releases the old subscription, and requires a new explicit
`earliest` or `latest` start. Fresh starts ignore historical consumer-group
commits; resume alone continues from committed offsets.

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

The first Loki read that uses the sealed incident trace is server-expanded to
the Connector root scope, so every allowed app participates in discovery. Loki
archives complete scope coverage, returned apps, misses, truncation state,
normalized business events, and a server-generated exact-trace correlation
assertion without exposing the trace or an enumerable trace hash. Later reads
may target a component identified by evidence. Native and source reads reuse
only an identical effective full-query fingerprint; different queries against
the same Connector or repository execute normally.

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

Kafka input uses `incident.alert.v2`. Unknown fields are rejected. The topic,
not the payload, selects the Workspace. Every event provides a producer
`source_event_id`, a correlation `dedup_key`, `event_kind`, component, and
environment. `trace_id` is an opaque optional string and is preserved exactly in
encrypted storage; `source_revision` is optional and, when provided, is the
authoritative full lowercase Git SHA for the Workspace alert-source repository.

```json
{
  "schema_version": "incident.alert.v2",
  "source_event_id": "alert-01",
  "dedup_key": "payment.order-create.failure",
  "event_kind": "firing",
  "occurred_at": "2026-08-27T10:38:59.522Z",
  "severity": "CRITICAL",
  "event": "payment.order_create.failed",
  "component": "payment-api",
  "environment": "production",
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
- manual incident creation, searchable/paginated incident list, canonical
  incident detail with server-driven lifecycle capabilities, immutable report
  reads/reviews, execution detail, nested run retry, follow-up actions, and SSE;
- authentication, users, invitations, health, and metrics.

`GET /incidents/{id}` is the canonical client state. It includes immutable
occurrences, investigation runs, the incident timeline, follow-up actions, and
server-computed allowed actions. `GET /investigations/{id}/report` is the full
immutable report view. SSE replays persisted
operation events by sequence, accepts `Last-Event-ID`, emits
`investigation.finished` for terminal state, and is used only to trigger a
canonical reload.

The Next.js Web app provides global model and AI-output-language settings,
Workspace topic/readiness settings, searchable direct account/repository
binding, and a searchable/filterable member list with row-level actions,
manual intake, a searchable incident list, and a responsive incident detail
that leads with incident state, occurrences, cause, diagnosis, evidence, and
next action. Investigation execution snapshots remain scoped to their immutable
run. Wide tables and tab lists scroll locally instead of widening the page.

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

Migration `0010_evidence_authority` intentionally clears investigations,
derived evidence/report/source/ResourceGraph data, and Workspace repository
bindings. Rebind repositories with the new analysis-mode/alert-source model
after upgrading. Active or paused ingestion returns to `draft` and must pass
readiness again before starting. The migration preserves Workspace, Git
catalogue/account, Connector, and model configuration and introduces no new
dependency.

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

All database-writing verification targets and pytest runs launched by Make use
a freshly migrated temporary PostgreSQL cluster on a private Unix socket and
destroy it afterward. Local PostgreSQL server tools (`initdb`, `pg_ctl`, and
`createdb`) must be available. Direct `uv run pytest` and direct write-capable
check scripts intentionally refuse to use the application database; use
`make test` and the targets below.

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
make test
uv run python scripts/check_forbidden_contracts.py
```

Schema verification must use a fresh PostgreSQL database and run both
`alembic upgrade head` and `alembic check`. The immutable baseline and forward
migrations are under `alembic/versions/`; never edit a revision after it has
been executed in an environment.

`make web-check` runs TypeScript validation, English/Chinese key parity and
untranslated-literal scanning, then the production Next.js build.

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
