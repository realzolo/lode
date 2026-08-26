# Lode Project Context

## Final Rebuild Status

The final replacement is being implemented from
`workplace/LODE_V1_FINAL_ARCHITECTURE_AND_DEVELOPMENT_PLAN.md`. Phases 0-7 are
the current completed implementation baseline:

- `contracts/v1` freezes the final Kafka, AI, evidence-read, control-plane,
  HTTP-surface, and database-inventory fixtures. These fixtures contain no
  compatibility aliases.
- `docs/threat-models/evidence-access.md` owns the LogQL,
  Elasticsearch/OpenSearch DSL, SQL, HTTPS, and command execution threat model
  and review checklist.
- `evals/v1` is the isolated, versioned release-test corpus. The Phase 0 smoke
  baseline is deterministic and does not contain production data or hidden
  model reasoning.
- `lode.contracts` provides the shared fixture validator and removed-contract
  scanner used by tests and command-line checks.
- `src/lode/domain` contains immutable,
  standard-library-only domain records for Workspaces, model portfolios,
  repository/build/component identity, ResourceGraph evidence, connector
  scopes, investigation snapshots, context bundles, model routing decisions,
  the four-part native-read audit chain, evidence artifacts, and observed
  relations, dynamic investigation decisions, server budgets, and operation
  results. Domain tests enforce canonical repository paths, independent
  provenance for verified identity, closed model/native-language roles,
  context headroom, hash-bound expiring read authorizations, allowed/rejected
  decision consistency, and explicit evidence for causal relations. The
  package must not import ORM, web, queue, transport, or provider libraries.
- The V1 ORM registry and the only migration register exactly the 67 tables in
  `contracts/v1/database/tables.json`. Provider accounts, model deployments,
  Workspace bindings, repositories, build units, components, resource graph
  revisions, connectors, immutable investigation snapshots, the evidence
  graph, native-read audit chain, source assessments, findings, and reports are
  separate final objects. Removed Application, Service, single-model, and
  product-specific integration tables are not registered; the standalone
  Service API and Application Service-binding routes were deleted.
- `contracts/v1/database/invariants.json` freezes 83 required triggers. The
  migration enforces timestamp updates, secret-free ordinary JSON,
  immutability, archived-investigation read-only behavior, authorization-chain
  integrity, frozen AI routing/context, exact source anchors, and confirmed
  report semantics. `EvidenceReadAttempt` is inserted only after execution in
  `succeeded`, `failed`, or `interrupted` state; retry creates a new immutable
  attempt rather than updating a running row.
- `scripts/check_schema.py` compares a migrated PostgreSQL catalog with the
  frozen table/trigger/FK contract. `scripts/check_database_behavior.py`
  performs rollback-only trigger checks plus a two-transaction uniqueness
  check. The final seed creates only Workspace, repository, provider/model,
  binding, and policy objects and is idempotent.
- `src/lode/application/intake.py` owns the only Kafka/manual normalization
  contract. `src/lode/infrastructure/intake_store.py` atomically resolves the
  Workspace from the Kafka topic and persists the alert, incident,
  investigation, immutable input, sealed values, graph snapshot, and durable
  job. `src/lode/consumer/main.py` commits Kafka offsets only after that
  transaction succeeds. Manual `POST /investigations` uses the same service.
- Intake has three independent idempotency boundaries: topic/partition/offset,
  Workspace/producer alert ID, and the active incident signature. Invalid and
  unassigned records are durably masked before optional Kafka DLQ mirroring;
  replay always runs the current strict validator. Concurrent offset and
  producer-ID races have one accepted result and one duplicate result.
- `src/lode/resource_understanding` provides the bounded, non-executing
  repository scanner, immutable scan values, deterministic identity validator,
  and transactional ResourceGraph publisher. It supports Node/pnpm, Python,
  Go, Cargo, Maven, Gradle, Docker, Kubernetes, Helm/compose metadata, nested
  workspaces, and multi-repository Components without running repository code.
- Repository discovery rejects symlinks, path escape, oversized/deep
  structures, duplicate YAML keys, YAML aliases/object constructors, and XML
  DTD/entities. `PyYAML` is a direct runtime dependency solely for safe YAML
  parsing; JSON, TOML, and XML use standard-library structured parsers.
- Build Units are created only for `runtime_source` bindings. Semantic
  annotations may reference scanner-owned observations and candidate paths but
  cannot create paths, repository bindings, access scopes, or credentials and
  never count as independent identity evidence. Cross-batch alias conflict
  deterministically downgrades identity to `ambiguous`.
- ResourceGraph publication is serialized by a Workspace row lock. Identical
  input reuses the current revision; changed input publishes an immutable child
  revision, records member differences and invalidation reasons, and never
  changes Repository bindings or EvidenceAccessScope. New investigations freeze
  the latest graph revision while existing investigation snapshots remain
  unchanged. Derived knowledge is exposed only through read-only Build Unit,
  Component, observation, identity-resolution, and graph revision routes.
- `src/lode/evidence_access` is the fail-closed native-read authorization
  kernel. Raw model JSON passes bounded UTF-8 decoding with duplicate-key,
  depth/node, unknown-field, payload/language, window, and sentinel validation.
  A language has no capability until a complete versioned parser/policy is
  explicitly registered; there is no regex or partial-parser fallback.
- Authorization re-reads the Investigation, native-read Operation, Connector
  Snapshot, and native-query AI invocation from PostgreSQL. It intersects
  evidence anchors, frozen scope, absolute investigation window, operation/
  result/timeout/output budgets, and hierarchical Workspace/connector/language/
  runner kill switches before any sealed value is opened.
- ValueRef plaintext is resolved only after policy allow, replaces one parser-
  approved value node, and must preserve the parsed structure. Candidate and
  decision JSON retain sentinels; the exact bound action exists only encrypted
  in `AuthorizedEvidenceRead`. Authorization capabilities use the independent
  `LODE_EVIDENCE_AUTHORIZATION_KEY`, expire quickly, bind all hashes, and are
  looked up by token hash.
- The execution adapter accepts only an internal `ExecutionPermit`, never raw
  candidate or model payload. A PostgreSQL advisory transaction lock plus a
  terminal immutable `EvidenceReadAttempt` permits one concurrent consumer;
  replay is rejected. Preflight/execution/cancellation and output byte
  postconditions produce terminal `succeeded`, `failed`, or `interrupted`
  attempts. Stable provider authentication, rate-limit, timeout, availability,
  partial-response, cost, invalid-response, and egress failures remain distinct
  execution failure codes in the immutable attempt instead of being relabeled
  as policy rejection. A successful normalized result is masked and archived
  as an `EvidenceCollection` and `EvidenceArtifact` in the same transaction
  before the immutable attempt can reference it; an archive savepoint prevents
  partial evidence from surviving a failed archive.
- `src/lode/evidence_connectors` is the native provider execution plane. Its
  provider-neutral registry activates independent Loki, Elasticsearch,
  OpenSearch, PostgreSQL, MySQL, generic safe-read HTTPS, and isolated command
  runner adapters plus all six native-language policies. The investigation core receives only
  candidates, authorization outcomes, and normalized evidence; it does not
  import or branch on provider products.
- LogQL is completely parsed by the fixed Apache-2.0 Grafana Lezer grammar in
  `tools/logql_parser`. The helper has no credential, database, network, or
  execution capability. Parser recovery nodes, trailing payloads, unsupported
  string encodings, multiple selectors, regex/pattern/format/label mutation,
  unknown CST nodes, unbounded metrics, and unregistered pipeline capabilities
  fail closed. Root matchers are injected from the frozen snapshot and exact
  ValueRef string nodes are reparsed after binding.
- Elasticsearch 8/9 and OpenSearch 2/3 use separate parser/policy versions,
  product verification, connector classes, and contract fixtures. Each permits
  only an exact single-index `_search`, a recursive positive query allowlist,
  bounded source projection, absolute timestamp filter, result/timeout/page
  limits, stable timestamp/ID order, and bounded bucket/metric aggregations.
  Script, runtime mapping, wildcard/regex/query-string, async/PIT/scroll,
  template, plugin, write, management, unknown, and partial-response paths are
  disabled.
- SQL is parsed with the exact `sqlglot==30.17.0` PostgreSQL/MySQL AST parser.
  It permits a bounded SELECT, one non-recursive read-only CTE, or an
  explain-only request; rejects unknown AST/function nodes, multiple statements,
  system schemas, unsafe joins, locking and write semantics; validates the exact
  table/column catalog; injects tenant/time predicates, stable ordering and
  LIMIT; and enforces EXPLAIN row/cost plus result row/byte budgets. PostgreSQL
  must attest a read-only replica and non-write-capable role; MySQL must attest
  both read-only flags and an exact SELECT/SHOW VIEW grant set. Execution uses
  explicit read-only transactions and server timeouts.
- Generic HTTPS accepts only cataloged GET/HEAD endpoints with canonical
  lowercase DNS origins, exact ports and typed path/query schemas. It has no
  request-body, redirect, proxy, credential-header, arbitrary content-type, or
  unchecked decompression capability. ValueRefs occupy complete query values;
  server-owned window, limit, and constant values cannot be overridden.
- `src/lode/command_runner` is an HMAC-authenticated, replay-protected process
  boundary for one fixed `rg --fixed-strings` profile. The worker is the only
  application service holding `LODE_COMMAND_RUNNER_KEY`; API and consumer do
  not receive it. The runner verifies the binary hash and argv grammar again,
  resolves a frozen working set without symlinks, and uses bubblewrap with no
  network, an empty environment, a read-only root, and only the authorized file
  mounted into the invocation. Compose gives it a private internal network,
  separate numeric uid, no capabilities, no-new-privileges, process/CPU/memory
  limits, a runner-specific seccomp deny profile, and no writable volume.
  High-risk secret output is discarded with
  only its SHA-256 and category retained. `LODE_COMMAND_RUNNER_ENABLED=false`
  is an independent fail-closed process kill switch. The deployment host must
  permit unprivileged user namespaces for bubblewrap; if it does not, command
  execution fails with `sandbox_violation` rather than falling back.
- Provider HTTPS transport accepts only adapter-owned relative paths, disables
  redirects, resolves DNS inside every TCP connection, pins the connection to a
  checked address while retaining the original TLS identity, checks every address
  against an explicit CIDR scope, rejects loopback/link-local/multicast/unspecified
  addresses, limits decompressed response bytes, and injects credentials only
  inside the adapter. Normalization validates duplicate-free bounded JSON,
  stable pagination and response shape, masks secret patterns, and marks prompt
  injection before evidence leaves the Connector Plane.
- Current implementation files use unversioned canonical names (`intake.py`,
  `investigation.py`, `check_schema.py`, and so on). The repository maintains
  one current implementation and never creates `_v1`/`_v2` module variants.
  Version literals remain only where an external wire or persisted schema
  contract requires them, such as `incident.alert.v1` and migration revision
  `0001_initial`.
- `src/lode/application/capabilities.py`, `decision_policy.py`,
  `evidence_graph.py`, and `investigation.py` implement the credential-free
  capability catalog, deterministic decision policy, evidence-backed graph
  projection, and serial dynamic decision waves. The service may trim or
  reject model-selected operations but never adds an unselected action.
- `src/lode/infrastructure/investigation_snapshots.py`,
  `investigation_store.py`, `investigation_leases.py`,
  `evidence_graph_store.py`, `evidence_archive.py`, and
  `native_read_executor.py` own frozen healthy connector capabilities,
  independent operation commits, replay reuse, scoped lease recovery,
  idempotent graph persistence, result archival, and the authorized native-read
  execution bridge. Connector credentials are hashed without decryption while
  snapshots are frozen; later control-plane edits cannot change an existing
  investigation.

Run `make contracts` (or `uv run python scripts/check_contracts.py`) whenever
a frozen contract or evaluation fixture changes. Run
`make schema-check` against an upgraded PostgreSQL database whenever ORM or
migration definitions change. Run `make intake-check` against an upgraded
PostgreSQL database whenever intake, encryption, idempotency, or replay changes.
Run `make resource-check` whenever scanning, identity validation, graph
publication, derived-resource views, or investigation graph snapshots change.
Run `make evidence-access-check` whenever candidate validation, policy,
ValueRef binding, authorization, execution permits, audit, or kill switches
change. The check writes immutable audit rows, so every invocation creates a
unique fixture identity and reports counts scoped to that invocation's
investigation; repeated runs against the same upgraded development database
must remain valid.
Run `make log-connectors-check` whenever LogQL parsing, search JSON policy,
provider config/version/introspection, HTTP serialization, pagination,
normalization, masking, or provider failure classification changes.
Run `make native-connectors-check` whenever any native parser/policy, SQL or
HTTPS adapter, command runner protocol/sandbox, connector registry, or native
deployment boundary changes.
Run `make investigation-check` whenever capability construction, decision
policy, dynamic waves, connector snapshots, graph projection/persistence,
operation replay, or worker leases change. Database checks that claim the
global job queue must run serially or against isolated databases.
Run
`uv run python scripts/check_forbidden_contracts.py` as the full-repository V1
removal gate; it is expected to become clean as the replacement phases delete
the currently implemented pre-final runtime contracts. Do not add an allowlist
or compatibility adapter to make this gate pass.

The sections below include final contracts for later implementation phases.
Phases 1-7 changed the database/control-plane identity architecture, the
migration/seed verification workflow, Kafka/manual intake, automatic resource
understanding, and the currently assembled API routes. Phase 3 added the direct
`PyYAML` dependency and the `resource-check` workflow. Phase 4 added an
independent authorization-key deployment requirement and the
`evidence-access-check` workflow, but no dependency. Phase 5 added the
`src/lode/evidence_connectors` execution plane, direct
`httpx` and `httpcore` runtime dependencies, fixed Node 24 LogQL parser build layer, three locked
npm parser dependencies, required Compose authorization-key propagation, and
the `log-connectors-check` workflow. Phase 6 added the fixed SQLGlot runtime
dependency, SQL/HTTPS/command policies and adapters, the isolated command-runner
image and private Compose network, independent runner key/kill switch, and the
runner-specific `deploy/command-runner-seccomp.json` syscall profile, and the
`native-connectors-check` workflow. Phase 7 added no dependency; it added the
dynamic investigation application layer, health-bearing connector snapshots,
Evidence Graph persistence, durable lease/replay behavior, native result
archival, three Prometheus instruments, and the `investigation-check` workflow.
Pre-final source/model/report, remaining API, and Web modules are not a
supported execution path while their
owning phases replace them; they must be rewritten without adapters.

Lode is an evidence-backed production incident investigation service. An
investigation advances through serial decision waves; independent operations
inside one wave may run concurrently with a hard limit of four. There are no
historical protocol or execution-path adapters.

## Runtime Components

- `src/lode/api`: FastAPI control plane, authorization, manual intake, investigation detail, audit pagination, and SSE.
- `src/lode/consumer`: strict Kafka `incident.alert.v1` validation and shared intake dispatch.
- `src/lode/worker`: durable job claiming, investigation-scoped leases, retry, and bounded cross-investigation concurrency.
- `src/lode/application/intake.py`: strict Kafka/manual validation and canonical normalization.
- `src/lode/infrastructure/intake_store.py`: transactional idempotency, masking, encrypted sealed values, immutable input, and job creation.
- `src/lode/resource_understanding/scanner.py`: bounded structured manifest scanner over frozen checkouts.
- `src/lode/resource_understanding/validator.py`: deterministic candidate, provenance, conflict, and authorization-boundary validation.
- `src/lode/resource_understanding/store.py`: idempotent multi-repository graph publication, invalidation, recovery, and materialized derived identity.
- `src/lode/evidence_access/candidate.py`: bounded strict native-read candidate input boundary.
- `src/lode/evidence_access/authorizer.py`: snapshot-owned parser/policy evaluation, immutable decision audit, ValueRef binding, and token issuance.
- `src/lode/evidence_access/orchestrator.py`: token-gated preflight/execution with replay defense and terminal attempts.
- `src/lode/evidence_access/logql.py`: maintained complete-CST LogQL policy, root-scope injection, metric budgets, and ValueRef reparse.
- `src/lode/evidence_access/elasticsearch.py`: Elasticsearch-specific structured JSON policy profile.
- `src/lode/evidence_access/opensearch.py`: OpenSearch-specific structured JSON policy profile.
- `src/lode/evidence_access/sql.py`: fixed-dialect AST proof, catalog scope, mandatory predicates, bounded CTE/EXPLAIN, and ValueRef reparse.
- `src/lode/evidence_access/https.py`: canonical safe-read endpoint and typed query policy.
- `src/lode/evidence_access/command.py`: exact fixed-argv and working-set policy.
- `src/lode/evidence_connectors/registry.py`: provider-neutral native policy/adapter composition root.
- `src/lode/evidence_connectors/loki.py`: Loki 3 verification, absolute-window scoped introspection, bounded query-range execution, and normalization.
- `src/lode/evidence_connectors/elasticsearch.py`: Elasticsearch 8/9 verification and bounded search adapter.
- `src/lode/evidence_connectors/opensearch.py`: OpenSearch 2/3 verification and bounded search adapter.
- `src/lode/evidence_connectors/postgresql.py`: replica/role-attested PostgreSQL read adapter.
- `src/lode/evidence_connectors/mysql.py`: topology/grant-attested MySQL read adapter with DNS-pinned sockets.
- `src/lode/evidence_connectors/https.py`: generic cataloged GET/HEAD adapter.
- `src/lode/evidence_connectors/command.py`: signed worker client for the isolated runner.
- `src/lode/command_runner`: replay-protected protocol, fixed executor, and minimal FastAPI process.
- `src/lode/evidence_connectors/transport.py`: redirect-free, DNS-pinned/CIDR-checked, byte-bounded HTTPS transport.
- `tools/logql_parser/parser.mjs`: credential-free Grafana Lezer LogQL CST helper.
- `src/lode/application/capabilities.py`: minimal frozen capability catalog and credential-free model view.
- `src/lode/application/decision_policy.py`: deterministic relevance, dependency, duplicate, resource, counter-evidence, and budget gates.
- `src/lode/application/investigation.py`: provider-neutral serial-wave orchestration ports and four-operation sibling isolation.
- `src/lode/application/evidence_graph.py`: deterministic timeline, entity matching, observations, and evidence-backed causal projection.
- `src/lode/infrastructure/investigation_snapshots.py`: immutable active/healthy connector capability freezing.
- `src/lode/infrastructure/investigation_store.py`: decision, step, operation, event, budget, and replay persistence.
- `src/lode/infrastructure/investigation_leases.py`: skip-locked job claims, heartbeat, retry, and expired-owner recovery.
- `src/lode/infrastructure/evidence_graph_store.py`: ownership-checked idempotent graph persistence.
- `src/lode/infrastructure/evidence_archive.py`: normalized native-result collection/artifact archival.
- `src/lode/infrastructure/native_read_executor.py`: dynamic operation to Evidence Access authorization/execution bridge.
- `src/lode/integration_policy.py`: extensible integration-kind registry, config/secret validation, capabilities, UI form metadata, and egress policy.
- `src/lode/engine/integrations.py`: provider adapters for verification and bounded snapshots.
- `src/lode/engine/evidence/git.py`: stack parsing, exact revision lookup, symbol range extraction, lexical candidates, and related-symbol expansion.
- `apps/web`: Next.js workbench with manual intake, wave/operation execution track, final-finding code viewer, and paginated audit drawer.

PostgreSQL is the source of truth. Kafka consumers only validate and enqueue;
workers execute investigations. The currently assembled FastAPI application
contains health, authentication, user/invite administration, and final manual
intake routes; later phases add the final investigation and Workspace control
surfaces. Model routing is frozen per investigation from its Workspace policy
and eligible deployments; there is no single-model or global-default fallback.

## Workspace Activation

The Workspace activation API is implemented in the control-plane phase. Every
transition into active Kafka ingestion must fail closed unless all three
conditions hold:

1. the Workspace has its required globally unique ingestion topic;
2. its active model policy can route every required role to an enabled,
   protocol-healthy deployment;
3. the broker can reach the configured topic.

Initial start and resume call the same backend readiness gate. Repository,
build-unit, component, ResourceGraph, and evidence-connector coverage remain
visible capabilities and evidence gaps; they do not block alert ingestion.
Missing requirements return HTTP 409 using the canonical business-error
envelope:

```json
{
  "error": {
    "code": "workspace_not_ready",
    "message": "Complete all required Workspace settings before starting ingestion.",
    "details": {"missing": ["topic", "model_roles", "broker_reachability"]}
  }
}
```

The Web start dialog displays the actual requirements and capability gaps. A
model deployment must pass its provider protocol probe before becoming routing
eligible; editing endpoint, credential, provider, or model resets its health to
`untested`. The backend gate remains authoritative for stale clients and direct
API callers.

## Investigation Execution

An investigation owns exactly one active decision wave:

1. Canonicalize and mask the complete error input.
2. Parse stack frames and the structured error contract.
3. Reload committed evidence, hypotheses, completed fingerprints, and remaining
   server budget.
4. Freeze active, healthy Connector capabilities once and build a minimal,
   credential-free server action catalog.
5. Ask the planner to finish or select one to four independent actions. An
   external action may include a provider-native candidate, while a decision
   may select zero external connectors.
6. Apply deterministic relevance, ValueRef provenance, dependency, duplicate,
   resource-conflict, counter-evidence, and server-budget policy. One structured
   repair is allowed after rejection.
7. Persist operation IDs and execute the allowed wave with at most four sibling
   operations.
8. Persist every operation result and evidence artifact independently; one
   failed operation does not cancel its siblings.
9. Project standard events, entities, relations, observations, and a stable
   timeline only from archived evidence. Shared trace membership alone never
   creates direction.
10. Start the next decision only from evidence committed by the prior wave.

Parallelism is allowed only inside an explicit wave and must remain at or below four operations. Allocate operation IDs/ordinals before launching work, use `return_exceptions=True`, and persist each result separately. Do not overlap decision waves. `engine_concurrency` separately controls how many investigations workers may run.

Limits are configured by:

- `investigation_max_evidence_steps`, default 12;
- `investigation_max_model_calls`, default 10;
- `investigation_max_native_reads`, default 8;
- `investigation_max_output_bytes`, default 8 MiB;
- `investigation_max_cost`, default 100;
- `investigation_timeout_seconds`, default 600.

Every action uses a server-generated fingerprint and may run once. Connector evidence reuse uses the complete immutable query fingerprint, including connector, endpoint, generated query, time window, direction and limit. Steps and operations commit independently. PostgreSQL permits one running step/decision wave while multiple operations in that wave may run. On worker recovery, completed evidence is reused and the first unfinished durable action resumes. Lease cleanup only changes jobs whose own leases expired.

Model output may select catalog IDs, cite archived evidence IDs, and propose
LogQL, Elasticsearch/OpenSearch Query DSL, SQL, safe HTTPS, or fixed command
candidates using only cataloged resources and ValueRefs. Every candidate still
passes the complete Evidence Access parser, ownership, relevance, budget,
ValueRef, authorization, preflight, execution, masking, and archive chain. The
model may never create credentials, connector configuration, access scope, or
repository authorization.

OpenAI-compatible base URLs are normalized to `/v1/chat/completions`; Anthropic base URLs are normalized to `/v1/messages`. Investigation requests use a configurable 120-second per-attempt timeout, provider-enforced strict JSON Schemas, and an 8192-token output bound; health probes keep a separate 30-second timeout. OpenAI-compatible providers use `response_format.json_schema`, while Anthropic uses a forced schema-bound tool result. Calls automatically retry only transient network failures, timeouts, HTTP 429, and HTTP 5xx with bounded exponential backoff. Authentication, request validation, and non-JSON protocol responses fail immediately. Each retry emits operation progress, and AI audit rows retain the actionable error classification and actual attempt count. Do not collapse a timeout or protocol error into a generic "model unavailable" message. If both the initial structured response and the single repair fail validation, report the analysis as unavailable with the exact contract error; never relabel output-format failure as insufficient evidence.

## Input Contract

Kafka accepts only strict `incident.alert.v1` with no compatibility aliases.
Required fields are `schema_version`, `alert_id`, timezone-aware `occurred_at`,
`severity`, stable `event`, arbitrary opaque `trace_id`, full lowercase
40-character `source_revision`, and structured `error` (`type`, `message`,
`stack`, recursive `cause`). Unknown fields are rejected, including removed
service, environment, request, commit, correlation, and carrier fields. The
Kafka topic selects the Workspace; no Workspace identifier is accepted from
the payload.

The original `trace_id`, including empty, whitespace, Unicode, or punctuation,
is never normalized. It is encrypted in the alert and sealed-value vault and
represented in ordinary investigation JSON only as
`<VALUE_REF:incident.trace_id>`. The complete recursive error is structurally
masked and archived without discarding stack or cause information.

Kafka processing persists before committing its offset. Redelivery is deduped
by topic/partition/offset, producer retry by `(workspace_id, alert_id)`, and an
already-active incident by its canonical Workspace/event/trace signature.
Invalid and unassigned payloads are durably masked. DLQ replay is atomic and
always uses the current validator; failed validation does not mark a record as
replayed.

Manual `POST /investigations` accepts `workspace_id`, timezone-aware
`occurred_at`, `severity`, `event`, optional opaque `trace_id`, optional
lowercase `source_revision`, structured `error`, and at most ten bounded typed
attachments. It requires Workspace `analyze` or `admin` permission (or global
admin) and calls the same normalization and persistence services as Kafka.
Removed service/environment/request fields fail strict validation.

`source_revision` is immutable alert/input evidence for resolving the alert's
source candidate. It is not a generic runtime commit for every repository.

## Source Investigation

Workspace, repository, build unit, component, and runtime resource are separate
identities. A Workspace binds repositories; scanner-produced BuildUnits and
Components plus ResourceGraph observations provide independently sourced
identity. Investigation creation freezes repository, component, resource,
connector-scope, model-policy, architecture-context, and graph-revision
snapshots. Identity is never learned from an inbound service-name header.

The alert `source_revision` may resolve only the alert's source repository.
Other repositories and components remain candidates until stack, build,
deployment, dependency, or connector evidence verifies their participation.
Unverified candidates are recorded as gaps and never promoted to facts.

Repository scanning always receives a frozen lowercase 40-character revision
and a binding namespace. It walks without following links, reads only bounded
known manifests, and never invokes a shell, repository script, package manager,
build tool, manifest command, or dynamic language loader. A structured scanner
observation may verify a Build Unit path; Component verification still requires
at least two independent structured provenance families. Annotation text is
untrusted data and cannot self-verify an identity.

External evidence access uses Workspace-owned `EvidenceConnector` rows with
separate immutable scope revisions, encrypted secrets, verification records,
and authorization/read audit objects. Connector kinds are code-registered and
may have multiple instances without a schema migration.

Native evidence capabilities are selected through the provider-neutral Connector
registry, never by an investigation-core product branch. The active kinds are
`loki`, `elasticsearch`, `opensearch`, `postgresql`, `mysql`, `https`, and
`command_runner`; each kind declares one native
language and its own verification, introspection, parser/policy versions, read
capabilities, adapter, fixture corpus, egress policy, and failure semantics. A
new provider is not active merely because it resembles an existing product: it
must register a complete independent security profile and contract tests.

Database connectors support PostgreSQL and MySQL through structured DNS host,
port, database, username, mandatory TLS, encrypted password, qualified
allowed-table catalog, AST-validated model candidates, and server-injected
predicates and budgets. The model never receives credentials or direct database
access, and an effective action can execute only through a one-use permit.
Kafka evidence connectors are independent of `Workspace.ingestion_topic` and
are limited to administrator-allowlisted topics and consumer groups.

All user-managed secrets are submitted as values, encrypted immediately with `LODE_DATA_ENCRYPTION_KEY`, stored separately from non-secret config, and never returned. Indirect environment-reference syntax is prohibited for integrations, AI keys, and Git credentials. The JWT signing key is never reused for encryption. Every integration endpoint must pass the application process egress allowlist and the deployment network policy must enforce the same boundary.

`LODE_EVIDENCE_AUTHORIZATION_KEY` is a third independent key and must differ
from both `LODE_SECRET_KEY` and `LODE_DATA_ENCRYPTION_KEY`. Missing, reused, or
non-positive-TTL authorization configuration fails before a native-read token
is issued. Only token hashes are persisted.

Workspace architecture context is configured beside its model policy. At
investigation creation, the current ordered context entries are masked and
frozen as an immutable snapshot. Every model phase receives that snapshot as
explicitly untrusted background: it may clarify boundaries and architecture,
but it cannot override system rules or independently prove an incident cause or
code defect. Later context edits affect only new investigations.

Native connector queries are generated only by server helpers from frozen
scope. AI-generated native candidates can reach a provider only through the
Evidence Access authorization chain. Raw trace values are resolved server-side
from sealed storage only after authorization and are never supplied to the
model. Log provider credentials support exactly one registered authentication
form, are injected after authorization, and never appear in requests, results,
ordinary config, or audit JSON.

Source lookup order is fixed:

1. Exact file, line, class, and function from the stack at the incident revision.
2. Incident-revision symbols and structured error identifiers.
3. Error code definitions and references.
4. Caller, callee, error branch, exception conversion, return checks, timeout, retry, and related tests.
5. Resolve each participating component only at the full revision independently
   observed for it. Multiple revisions remain separate incident observations.

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

The project has not released its database baseline.
`alembic/versions/0001_initial.py` is the only revision and creates exactly the
67 final V1 business tables. There is no V2 migration, compatibility view,
dual write, backfill, or old-schema adapter; development databases are
recreated from V1. Freeze V1 only after the first release, then use forward
migrations.

The table inventory and database invariants are frozen independently under
`contracts/v1/database`. SQLAlchemy metadata must exactly match the migration.
The schema currently owns 83 explicit non-internal triggers: 35 immutable-row
triggers, 25 archived-investigation read-only triggers, 16 `updated_at`
triggers, and seven cross-table/security triggers. `set_updated_at()` uses
`clock_timestamp()` so updates within one transaction still advance the value.
Ordinary connector/scope JSON is traversed structurally and rejects credential
keys at any object depth.

Native-read attempts are terminal immutable audit records. An executor inserts
one after the operation finishes, with a non-null `finished_at`; success has no
failure payload, while failure/interruption requires a stable failure code.
Retries insert a new `(authorized_read_id, attempt)` row.

`audit_events` is immutable. Its actor and Workspace foreign keys use
`ON DELETE RESTRICT`, not `SET NULL`, because cascading nullification would
rewrite historical audit identity. Users and Workspaces referenced by audit are
disabled rather than physically deleted.

Until V1 is released, model changes are folded into `0001_initial.py`. Verify a fresh schema:

```bash
LODE_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/lode_migration_test \
LODE_SECRET_KEY=test-secret \
LODE_DATA_ENCRYPTION_KEY=test-data-encryption-key \
LODE_EVIDENCE_AUTHORIZATION_KEY=test-evidence-authorization-key \
uv run alembic upgrade head
uv run alembic check
uv run python scripts/check_schema.py
uv run python scripts/check_database_behavior.py
uv run python scripts/check_intake.py
uv run python scripts/check_resource_graph.py
uv run python scripts/check_evidence_access.py
make log-connectors-check
make native-connectors-check
```

Alembic autogeneration against that database must produce no schema difference.
The behavior checker rolls back all fixtures, including its concurrent unique
topic writes. The intake checker verifies strict validation, all dedupe layers,
concurrent races, durable DLQ/unassigned handling, current-validator replay,
manual HTTP intake, and exact encrypted trace round trips. `uv run python
scripts/check_resource_graph.py` verifies single/monorepo and multi-repository
identity, non-runtime repository exclusion, recovery reuse, alias conflict,
revision invalidation, immutable historical membership, investigation snapshot
freezing, access-boundary preservation, and the authenticated read-only graph
view. `uv run python scripts/seed.py` is idempotent and may only create final
control-plane models.

The evidence-access checker constructs the full snapshot/model/operation/audit
chain through the production Elasticsearch policy. It verifies exact opaque
ValueRef round trips, mandatory timestamp/source/sort constraints, budget
shrinkage, unsupported SQL-node rejection, kill switch rejection, duplicate
fingerprint rejection, encrypted-only effective actions, signed hash-bound
tokens, forged permit rejection, one adapter call under concurrent replay, and
terminal immutable success and provider-rate-limit attempts.

`make log-connectors-check` runs the fixed LogQL CST, Elasticsearch/OpenSearch
JSON policy, provider request/response fixture, version isolation,
budgeted introspection, stable pagination, partial response, timeout/rate/auth/5xx,
ValueRef injection, aggregation cost, masking, prompt-injection marking,
registry, egress-config, and forged-permit tests. Run `make install` first; it
performs both `uv sync --all-extras` and the locked, script-disabled npm install
for `tools/logql_parser`. The backend Docker image builds that parser under
Node 24 and copies only its runtime plus the Node executable into the Python
image.

`make native-connectors-check` additionally runs the PostgreSQL/MySQL AST and
replica/grant-attestation suites, generic HTTPS canonicalization/SSRF and
endpoint-schema corpus, command argv/path corpus, signed runner protocol,
replay and process kill switches, exact-file bubblewrap mapping, high-risk
secret hashing, and Compose privilege/network/key-ownership assertions.

## Frontend Contract

Application creation atomically requires both name and Kafka ingestion topic; the topic cannot be cleared later. The application settings navigation exposes one `Integrations` page rather than a top-level database page. It renders a dense, filterable instance list and builds create/edit forms from `GET /integration-kinds`, so a new kind does not require a frontend conditional. Each row exposes kind, capabilities, revision, verification/state, and instance operations without showing secrets. Application admins, not only global admins, may manage their application's integrations.

Model selection and architecture context share one `Model & Context` page. There is no standalone descriptions route or navigation item. Application admins manage both controls in this workspace; context is presented as model background rather than as a peer application resource.

The investigation workbench displays the incident cause first and code diagnosis second. Only source artifacts referenced by final validated code findings may appear in the main code viewer; lexical, context, and intermediate source candidates remain available only as masked audit references. The code viewer is read-only, follows the current application theme, and highlights the exact finding range alongside where it is wrong, why, trigger, propagation, expected behavior, missing validation, and test.

Execution uses a vertical decision-wave track and shows sibling operations within each wave. Every operation exposes its purpose, masked input, progress events, actual result, duration, failure, metrics, and evidence links. Running states use restrained motion with a reduced-motion fallback. Initial data and Monaco loading surfaces reserve stable dimensions, and transient SSE refresh failures preserve the last canonical result instead of replacing the page. Waiting and running states have stable dimensions on desktop, tablet, and mobile.

Do not restore a second investigation UI or translate historical response shapes.

## Development And Verification

Backend:

```bash
make install
export LODE_DATA_ENCRYPTION_KEY='replace-with-an-independent-random-secret'
export LODE_EVIDENCE_AUTHORIZATION_KEY='replace-with-a-third-independent-random-secret'
export LODE_COMMAND_RUNNER_KEY='replace-with-a-fourth-independent-random-secret'
make contracts
make intake-check
make resource-check
make evidence-access-check
make log-connectors-check
make native-connectors-check
make investigation-check
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

For investigation changes, tests must cover strict `incident.alert.v1` parsing,
opaque trace preservation/sealing, Workspace permissions, all intake idempotency
boundaries, generated native queries, full query fingerprints, bounded wave
concurrency and partial failure, independently observed source revisions, full
error preservation, exact stack/source range, candidate rejection, incident
revision gates, strict model response schemas, independent semantic downgrade,
external cause separation, cross-investigation concurrency, transient AI retry
classification, scoped lease recovery, idempotent resume, manual retry lineage,
archive immutability, operation detail, SSE replay, audit pagination,
permissions, masking, and fresh schema creation.

Resource-understanding changes must additionally cover single repositories,
nested Node/Python/JVM workspaces, multi-repository Components, non-runtime
repositories, path/symlink/YAML/XML attacks, bounded structure parsing,
provenance independence, within/cross-publication alias conflicts, annotation
authorization expansion, observation/publication idempotency, invalidation,
immutable graph membership, job recovery, and investigation snapshot isolation.

Evidence-access kernel changes must cover duplicate/oversized/deep/invalid
candidate JSON, every stable rejection class, snapshot ownership, missing or
partial parser behavior, scope and budget intersection, arbitrary ValueRef
strings and injection shapes, authorization key separation, token tamper/
expiry/replay, fingerprint dedupe, kill switches, forged execution permits,
preflight/execution/cancellation terminals, output bounds, and immutable audit.

Log Connector changes must additionally cover complete LogQL CST parsing and
parser differential inputs, root selector enforcement, exact string-node
ValueRef binding, bounded log and metric queries, exact index and field scope,
recursive Query DSL and aggregation allowlists, bucket/cardinality limits,
independent Elasticsearch/OpenSearch version proof, schema introspection,
provider request snapshots, stable pagination/order, partial and malformed
responses, timeout/429/5xx/auth classification, DNS/CIDR/redirect/byte egress
controls, secret masking, prompt-injection marking, and provider-neutral core
imports.

SQL/HTTPS/Command changes must additionally cover PostgreSQL and MySQL dialect
AST differentials, safe CTE and non-executing EXPLAIN, write/locking/function/
system-catalog rejection, replica and grant attestation, read-only transaction
and cost budgets, canonical URL/SSRF/DNS/redirect/decompression controls, exact
endpoint schemas, argv and ValueRef injection, binary attestation, symlink/path
escape, signed protocol replay, exact-file read-only mounts, empty environment,
private network and secret ownership, process kill switch, output truncation,
high-risk secret hashing, and infrastructure behavior when policy input is
forged.

Dynamic investigation changes must additionally cover credential-free
capability catalogs, zero unselected connector calls, changing connector choice
after newly committed evidence, dependency/resource conflict trimming,
counter-evidence and budget gates, one repair only, four-operation concurrency,
partial sibling failure, terminal replay reuse without budget growth, frozen
connector health/scope, artifact-before-attempt archival, graph causal rules,
unknown/ambiguous entities, idempotent graph persistence, skip-locked claims,
heartbeats, and expired-lease recovery.

Before declaring work complete, assess architecture, dependency, and development-workflow impact. Update this file immediately when any of those contracts change, then verify the documented commands and behavior match the implementation.
