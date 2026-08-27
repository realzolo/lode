# Lode Project Context

## Final Rebuild Status

The final replacement is being implemented from
`workplace/LODE_V1_FINAL_ARCHITECTURE_AND_DEVELOPMENT_PLAN.md`. Phases 0-7 are
the completed baseline. The Phase 8 implementation candidate is present, but
Phase 8 remains release-gated until frozen provider-run observations satisfy the
quality and Wilson confidence thresholds below. The final Phase 9 API and Web
Workbench implementation is present and passes its deterministic API, database,
SSE, type, build, and responsive-browser checks. Phase 10 local hardening and
the deterministic release gate pass on a fresh isolated database; the latest
full backend run is 353 tests. The complete gate includes deterministic fuzz,
security, worker soak/crash/lease-loss, release-bundle, operational-metric, and
canary mechanism tests. Final release still requires frozen real-provider and
deployment-canary observations to pass the statistical and non-regression gate.

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
- The current ORM registry and the only migration register exactly the 72 tables in
  `contracts/v1/database/tables.json`. Provider accounts, account models,
  static Git adapters/accounts/catalogue, Workspace Git grants and repository
  entitlements, Workspace bindings, build units, components, resource graph
  revisions, connectors, immutable investigation snapshots, the evidence graph,
  native-read audit chain, source assessments, findings, and reports are
  separate final objects. Deprecated global workload identity, single-model,
  per-repository Git credentials, and product-specific integration tables and
  routes are not registered.
- Git adapters are static reviewed code registrations. Token-only Git accounts,
  encrypted immutable credential revisions, and discovered repository facts are
  global reusable objects. A
  global admin explicitly grants an account to a Workspace and selects its
  repository entitlements; all cross-Workspace grant, entitlement, binding, and
  credential-revision references are database-constrained. Private repositories
  are accessed only through the approved account connection, never through a
  per-repository secret. GitHub, GitLab, and Gitee account tokens are verified
  before their repository catalogues are used. GitHub Enterprise Server and
  GitLab Self-Managed may override the API root; Gitee is official-endpoint only.
- `contracts/v1/database/invariants.json` freezes the required trigger inventory. The
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
  result/timeout/output budgets, and the current Connector lifecycle before any
  sealed value is opened. All registered native languages and adapters are
  candidates by default; the planner decides whether they are relevant. A
  disabled or unhealthy Connector rejects only future authorization, while a
  frozen snapshot prevents later reactivation from expanding the investigation.
- ValueRef plaintext is resolved only after policy allow, replaces one parser-
  approved value node, and must preserve the parsed structure. Candidate and
  decision JSON retain sentinels; the exact bound action exists only encrypted
  in `AuthorizedEvidenceRead`. Authorization capabilities use a domain-separated
  key derived from `LODE_MASTER_KEY`, expire quickly, bind all hashes, and are
  looked up by token hash.
- The execution adapter accepts only an internal `ExecutionPermit`, never raw
  candidate or model payload. A PostgreSQL advisory transaction lock plus a
  terminal immutable `EvidenceReadAttempt` permits one concurrent consumer;
  replay is rejected. Preflight/execution/cancellation and output byte
  postconditions produce terminal `succeeded`, `failed`, or `interrupted`
  attempts. Stable provider authentication, rate-limit, timeout, availability,
  partial-response, cost, and invalid-response failures remain distinct
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
- Generic HTTPS accepts only cataloged GET/HEAD endpoints with canonical HTTPS
  origins, exact ports and typed path/query schemas. It has no
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
  High-risk secret output is discarded with only its SHA-256 and category
  retained. The deployment host must
  permit unprivileged user namespaces for bubblewrap; if it does not, command
  execution fails with `sandbox_violation` rather than falling back.
- Provider HTTPS transport accepts only adapter-owned relative paths, disables
  redirects, limits decompressed response bytes, and injects credentials only
  inside the adapter. Normalization validates duplicate-free bounded JSON,
  stable pagination and response shape, masks secret patterns, and marks prompt
  injection before evidence leaves the Connector Plane.
- AI completion, model-catalog traffic, and remote Git use their configured
  endpoint directly. Lode does not impose application-level host, CIDR, DNS,
  or network-egress restrictions.
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
- Intake now freezes repository resolution, connector instances/scopes, eligible
  model binding revisions, model policy, context policy, architecture context,
  and graph identity inside the same transaction as the investigation and job.
  Repository snapshots retain the exact URL, read-only credential identity,
  full SHA, revision role, and resolution status. Model policy
  `eligible_bindings` contains explicit `{binding_id, revision}` objects; an ID
  or revision mismatch fails closed instead of selecting current control-plane
  state.
- `src/lode/infrastructure/git_source.py`, `source_executor.py`, and
  `source_store.py` provide shell-free, bounded, disposable exact-revision source
  reads. The alert SHA is never replaced by a default branch. Multi-repository
  exact matches remain ambiguous, secondary/default-branch matches are search
  candidates, and archived source artifacts retain repository snapshot, SHA,
  role, path, symbol, line range, masking, and immutable assessment provenance.
- `src/lode/application/model_routing.py`, `context.py`, and
  `context_compaction.py` plus `src/lode/infrastructure/model_runtime.py` own
  frozen role/execution-class routing, per-investigation and per-binding call/
  cost limits, `tiktoken` counting of the complete serialized OpenAI request,
  immutable context bundles, audited
  replay, and one bounded compaction retry. Pinned input and counter-evidence are
  never tail-truncated. Compaction rejects reference, number, timestamp, SHA, or
  identity drift; hidden reasoning, raw provider output, sessions, and provider
  caches never cross role/model boundaries.
- Planner, synthesizer, verifier, and context compactor are separate audited
  invocations. Simple tasks route to eligible latency account models; conflict,
  multi-component/repository, deep causal, synthesis, and verification tasks
  require reasoning account models. A route with no eligible frozen candidate is
  persisted with every exclusion and zero capacity before returning unavailable.
  Provider/account-model drift cannot silently admit a replacement model.
- `src/lode/infrastructure/report_store.py` is the sole report publisher. It
  validates strict structured synthesis/verifier payloads, investigation-owned
  evidence, exact source provenance, runtime configuration authority, frozen
  verifier independence, and immutable finding/report hashes. Confirmed code
  requires exact or independently corroborated runtime source plus verifier
  approval; configuration without runtime evidence, source contradiction,
  verifier absence/failure/rejection, or provenance mismatch downgrades or rejects
  the conclusion. External incident causes do not require a fabricated code
  finding. Repeated publication reuses identical immutable findings.
- `src/lode/worker/main.py` composes the production planner, native/source
  operation executor, dynamic orchestrator, synthesizer, verifier, and report
  publisher by default. Frozen connector secrets are encrypted strict JSON
  objects whose keys and values are strings; duplicate keys, decryption failure,
  instance revision drift, or ciphertext hash drift make the connector
  unavailable without a plaintext or current-state fallback.
  The worker acquires an engine slot before claiming a job, so claimed and
  actively handled investigations share the same hard concurrency bound. Lease
  heartbeat loss cancels the handler and never completes or fails a job no
  longer owned; expired work is resumed only through durable lease recovery.

Run `make contracts` (or `uv run python scripts/check_contracts.py`) whenever
a frozen contract or evaluation fixture changes. Run
`make schema-check` against an upgraded PostgreSQL database whenever ORM or
migration definitions change. Run `make intake-check` against an upgraded
PostgreSQL database whenever intake, encryption, idempotency, or replay changes.
Run `make resource-check` whenever scanning, identity validation, graph
publication, derived-resource views, or investigation graph snapshots change.
Run `make evidence-access-check` whenever candidate validation, policy,
ValueRef binding, authorization, execution permits, audit, or Connector
lifecycle checks change. The check writes immutable audit rows, so every invocation creates a
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
Run `make analysis-check` whenever repository resolution/source archival, model
policy/binding snapshots, routing, tokenizer/context assembly, compaction,
planner roles, synthesis/verification, authority gates, or report publication
changes. It runs the deterministic quality smoke suite and a repeatable real-
database execution checker. `make provider-release-check` is the strict
provider-run release gate. It requires candidate quality observations and run
manifest, operational observations and frozen baseline, distinct canary
baseline observations and run manifest, and a SHA-256-bound release bundle.
Each row needs a globally unique
`observation_id`; repeated independent runs may use the same frozen `case_id`,
but the observation set must still cover every frozen case. The six-case
deterministic smoke corpus intentionally reports its Wilson confidence as
insufficient for release rather than pretending a small sample is statistically
valid. The operational gate additionally enforces identity precision,
deterministic identity correctness, malicious/valid native-read corpora, frozen
Connector-selection and model-routing baselines, and aggregate plus per-case
canary non-regression. Synthetic observations exercise the mechanism only and
never count as release evidence.
Run
`uv run python scripts/check_forbidden_contracts.py` as the full-repository
removal gate; it is expected to become clean as the replacement phases delete
the currently implemented pre-final runtime contracts. Do not add an allowlist
or compatibility adapter to make this gate pass. It also rejects versioned
implementation filenames such as `*_v1.py`; protocol directories/literals
remain versioned where the frozen wire contract requires it.

The sections below describe the current implementation and the remaining release
requirements. Phases 1-9 changed the database/control-plane identity
architecture, migration/seed verification workflow, Kafka/manual intake,
automatic resource understanding, API surface, and Web Workbench. Phase 3 added the direct
`PyYAML` dependency and the `resource-check` workflow. Phase 4 added an
independent authorization-key deployment requirement and the
`evidence-access-check` workflow, but no dependency. Phase 5 added the
`src/lode/evidence_connectors` execution plane, direct
`httpx` and `httpcore` runtime dependencies, fixed Node 24 LogQL parser build layer, three locked
npm parser dependencies, required Compose authorization-key propagation, and
the `log-connectors-check` workflow. Phase 6 added the fixed SQLGlot runtime
dependency, SQL/HTTPS/command policies and adapters, the isolated command-runner
image and private Compose network, independent runner key and lifecycle check, and the
runner-specific `deploy/command-runner-seccomp.json` syscall profile, and the
`native-connectors-check` workflow. Phase 7 added no dependency; it added the
dynamic investigation application layer, health-bearing connector snapshots,
Evidence Graph persistence, durable lease/replay behavior, native result
archival, three Prometheus instruments, and the `investigation-check` workflow.
Phase 8 added no dependency; it added same-transaction repository/connector/model
control snapshots, exact Git source reads, authority assessment, frozen multi-
model routing, exact context and validated compaction, audited role isolation,
strict synthesis/verifier publication, the default worker composition root, and
the `analysis-check` workflow. Phase 9 added no dependency; it replaced the API
and Web surfaces with the final Workspace/model/repository/connector/investigation
contracts and added the `api-check` and `web-check` workflows.
The current unreleased Git-account/catalogue and typed-connector redesign adds
no dependency. It replaces local repository and per-repository credential
control with global provider/account management and Workspace authorizations;
it also removes raw connector configuration/scope forms and the public command
runner connector. Phase 10 adds no dependency. It adds startup master-key and Runner-key
validation, correlation IDs and isolated-
runner nonces, slot-before-claim worker concurrency, lease-loss cancellation,
deterministic fuzz/security/performance/soak checks, complete operational and
canary release evaluation, artifact hash binding, expanded low-cardinality
Prometheus metrics, and the `hardening-check`, `local-release-check`, and
`provider-release-check` workflows. README, evaluation documentation,
environment examples, Compose, and this context document must remain
synchronized with those deployment changes. Compose declares the project name
as `lode`, so generated resources remain namespaced (`lode-...`) independently
of the checkout directory. Service keys remain role-oriented (`postgres`,
`kafka`, `app`, and so on) because they are the stable in-network DNS names;
`container_name` must not be added solely to remove Compose's replica suffix
such as `-1`, since that disables safe scaling and can create name conflicts.
The committed Compose template is `docker-compose.example.yml`; the local
`docker-compose.yml` is ignored and must be created from that template before
running the stack.

The account-model revision adds the direct `tiktoken` runtime dependency. It
uses explicit OpenAI Responses, OpenAI Chat Completions, and Anthropic Messages
protocol accounts with reviewed catalog-backed account models and a strict
structured-output health probe. It
also requires `make contracts`, `make schema-check`, `make api-check`,
`make analysis-check`, and `make web-check` for changes in this surface.

Lode is an evidence-backed production incident investigation service. An
investigation advances through serial decision waves; independent operations
inside one wave may run concurrently with a hard limit of four. There are no
historical protocol or execution-path adapters.

`src/lode/metrics.py` owns the process-wide monitoring contract. It covers
Kafka intake/active Workspace/heartbeat, queue depth and claim latency, lease
recovery, investigation and operation outcomes, native authorization stages,
query bytes/scan stats/cost, decision estimated/actual cost, resource identity
and invalidation, source resolution/mismatch, AI protocol/routing/context/
compression/token/capacity, and SSE connection/replay lag. Labels contain only
closed roles, kinds, providers/adapters, outcomes, and stable codes; never IDs,
trace values, prompts, endpoints, or other unbounded data.

## Runtime Components

- `src/lode/api`: FastAPI control plane, global AI-output-language settings,
  authorization, manual intake, human-readable investigation detail, explicit
  technical detail, audit pagination, and SSE.
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
- `src/lode/evidence_connectors/mysql.py`: topology/grant-attested MySQL read adapter.
- `src/lode/evidence_connectors/https.py`: generic cataloged GET/HEAD adapter.
- `src/lode/evidence_connectors/command.py`: signed worker client for the isolated runner.
- `src/lode/command_runner`: replay-protected protocol, fixed executor, and minimal FastAPI process.
- `src/lode/evidence_connectors/transport.py`: redirect-free, byte-bounded HTTPS transport.
- `tools/logql_parser/parser.mjs`: credential-free Grafana Lezer LogQL CST helper.
- `src/lode/application/capabilities.py`: minimal frozen capability catalog and credential-free model view.
- `src/lode/application/decision_policy.py`: deterministic relevance, dependency, duplicate, resource, counter-evidence, and budget gates.
- `src/lode/application/investigation.py`: provider-neutral serial-wave orchestration ports and four-operation sibling isolation.
- `src/lode/application/investigation_policy.py`: server-owned `fast`,
  `balanced`, and `deep` immutable investigation policy profiles.
- `src/lode/application/evidence_graph.py`: deterministic timeline, entity matching, observations, and evidence-backed causal projection.
- `src/lode/infrastructure/investigation_snapshots.py`: immutable active/healthy connector capability freezing.
- `src/lode/infrastructure/investigation_store.py`: decision, step, operation, event, budget, and replay persistence.
- `src/lode/infrastructure/investigation_leases.py`: skip-locked job claims, heartbeat, retry, and expired-owner recovery.
- `src/lode/infrastructure/evidence_graph_store.py`: ownership-checked idempotent graph persistence.
- `src/lode/infrastructure/evidence_archive.py`: normalized native-result collection/artifact archival.
- `src/lode/infrastructure/native_read_executor.py`: dynamic operation to Evidence Access authorization/execution bridge.
- `src/lode/application/model_routing.py`: deterministic selection over frozen binding snapshots and server-owned task complexity.
- `src/lode/application/context.py`: exact-token, role-isolated context assembly without hidden provider state.
- `src/lode/application/context_compaction.py`: drift-rejecting layered summary validation with pinned counter-evidence.
- `src/lode/application/source_authority.py`: exact/runtime source and configuration authority rules.
- `src/lode/application/conclusion_validation.py`: server-owned confirmation downgrade gates.
- `src/lode/infrastructure/investigation_control_snapshots.py`: same-transaction repository and model control freezing.
- `src/lode/infrastructure/model_runtime.py`: immutable routing/context/invocation audit, replay, drift checks, and bounded compaction.
- `src/lode/git_accounts`: bounded token-only GitHub/GitLab/Gitee account and
  repository-catalogue adapters plus strict credential encoding. New adapters
  must add endpoint policy, token headers, pagination, normalization, error
  classes, and private-repository regression coverage in the same change.
- `src/lode/infrastructure/git_source.py`: bounded exact-revision HTTPS Git reader.
- `src/lode/infrastructure/source_store.py`: masked source artifact, revision, and assessment archival.
- `src/lode/infrastructure/investigation_reporting.py`: audited synthesis and independent verification roles.
- `src/lode/infrastructure/report_store.py`: strict semantic validation and immutable report publication.
- `src/lode/infrastructure/connector_resolver.py`: frozen connector/secret verification and production adapter construction.
- `src/lode/infrastructure/operation_executor.py`: provider-neutral native/source operation dispatch.
- `src/lode/integration_policy.py`: extensible integration-kind registry, config/secret validation, capabilities, and UI form metadata.
- `src/lode/engine/integrations.py`: provider adapters for verification and bounded snapshots.
- `src/lode/engine/evidence/git.py`: stack parsing, exact revision lookup, symbol range extraction, lexical candidates, and related-symbol expansion.
- `apps/web`: Next.js global model/output-language and Workspace control plane
  plus the investigation Workbench for manual intake, summary-first detail,
  evidence, execution audit, explicit technical detail, retry, archive, and
  SSE refresh. All user-visible product text is served through `next-intl`.

PostgreSQL is the source of truth. Kafka consumers only validate and enqueue;
workers execute investigations. FastAPI exposes health, authentication,
system-administrator user and Workspace-member administration, global
provider-account/model administration, Workspace policy/repository/connector
control, and a separate ordinary-user Workbench API for manual intake,
investigation reads, audit, retry, and SSE. The singleton
`platform_settings` row selects the output language for newly created
investigations; each investigation freezes that language. Model routing is frozen per
investigation from its Workspace policy and eligible account models; there is no
single-model or global-default fallback.

## Workspace Activation

The Workspace activation API is implemented in the control-plane phase. Every
transition into active Kafka ingestion must fail closed unless all three
conditions hold:

1. the Workspace has its required globally unique ingestion topic;
2. its active model policy can route every required role to an active,
   protocol-healthy account model;
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
account model must pass its provider protocol probe before becoming routing
eligible; editing endpoint, credential, provider, or model resets its health to
`untested`. The backend gate remains authoritative for stale clients and direct
API callers.

## Investigation Execution

An investigation owns exactly one active decision wave:

1. Canonicalize and mask the complete error input.
2. Parse stack frames and the structured error contract.
3. Reload committed evidence, hypotheses, completed fingerprints, and remaining
   server budget.
4. Use the repository, Connector, scope, model binding/policy, context, and graph
   snapshots frozen atomically at intake; build a minimal credential-free server
   action catalog.
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

Parallelism is allowed only inside an explicit wave and must remain at or below four operations. Allocate operation IDs/ordinals before launching work, use `return_exceptions=True`, and persist each result separately. Do not overlap decision waves. `LODE_WORKER_CONCURRENCY` separately controls how many investigations workers may run.

Investigation limits are immutable Workspace policy revisions, selected only as
one of three server-owned profiles: `fast` (6 evidence steps, 5 model calls,
4 native reads, 2 MiB, cost 25, 300 seconds), `balanced` (12, 10, 8, 8 MiB,
100, 600 seconds), or `deep` (16, 14, 12, 16 MiB, 200, 900 seconds). New
investigations freeze the selected revision; no budget is supplied through
environment variables or model-policy JSON.

## Configuration Authority

`Settings` accepts only required deployment topology and trust-boundary values:
database/Kafka endpoints, worker process concurrency, source-cache location,
one master key, and the isolated Runner key. CORS accepts every origin and
Lode applies no application-level outbound network restrictions. Unknown
`LODE_*` variables fail startup. Timeouts, retries, parser paths, request-size
limits, and scheduling intervals are reviewed code constants in
`src/lode/runtime_defaults.py`; they are not end-user configuration. Functional
capabilities have no feature-specific boolean switches. Registered abilities are available
to the planner, and Connector lifecycle/health plus per-read authorization are
the only execution gates.

`platform_settings` is a singleton revisioned product setting. Its
`ai_output_language` is restricted to the system-supported language list
(`en`, `zh`); it applies only to new investigations and is frozen into every
investigation and all model role prompts.

Every action uses a server-generated fingerprint and may run once. Connector evidence reuse uses the complete immutable query fingerprint, including connector, endpoint, generated query, time window, direction and limit. Steps and operations commit independently. PostgreSQL permits one running step/decision wave while multiple operations in that wave may run. On worker recovery, completed evidence is reused and the first unfinished durable action resumes. Lease cleanup only changes jobs whose own leases expired.

Model output may select catalog IDs, cite archived evidence IDs, and propose
LogQL, Elasticsearch/OpenSearch Query DSL, SQL, safe HTTPS, or fixed command
candidates using only cataloged resources and ValueRefs. Every candidate still
passes the complete Evidence Access parser, ownership, relevance, budget,
ValueRef, authorization, preflight, execution, masking, and archive chain. The
model may never create credentials, connector configuration, access scope, or
repository authorization.

Model accounts use an explicit closed `protocol_id`: `openai.responses.v1`,
`openai.chat_completions.v1`, or `anthropic.messages.v1`. Each accepts a
credential-free HTTPS BaseURL, including a compatible gateway or private
endpoint, but URL shape never selects a protocol. Accounts store only a
write-only API key, use protocol-defined paths and authentication headers, and
select exact model IDs from the reviewed provider/protocol catalog. Create,
connection updates, and model-set updates run a restricted structured-output
probe for every selected model before it becomes routable. The only routing
targets are reviewed fixed catalog IDs. Each account model records
`synced`/`manual`/`missing` discovery and
`untested`/`healthy`/`unavailable` protocol health; an upstream disappearance
soft-disables a synced model without removing the audit record, while a manual
model remains until explicit removal or a failed probe. `List models` never
proves completion compatibility: `POST /ai-provider-accounts/{id}/models/{model_id}/test`
uses Chat Completions and must succeed before routing can select it.

The catalog, not user input, owns `context_window_tokens`,
`max_output_tokens`, tokenizer encoding, safety margin, capabilities, catalog
revision, and profile hash. Investigation snapshots freeze the account-model
ID and revision, account revision, and all catalog values. Runtime uses
`tiktoken` to count the full compact serialized OpenAI request and passes the
routed `allowed_output_tokens` as `max_completion_tokens`; no global output
limit, user tokenizer ID, or user model token limits exist. Provider-enforced
strict JSON Schemas and the selected model's output/headroom limits are
mandatory. Calls automatically retry only transient network failures, timeouts,
HTTP 429, and HTTP 5xx with bounded exponential backoff. Authentication,
request validation, and non-JSON protocol responses fail immediately. Each
retry emits operation progress, and AI audit rows retain the actionable error
classification and actual attempt count. Provider-reported usage is retained;
a post-call estimate is audit metadata only and never admits an oversized
context. Do not collapse a timeout or protocol error into a generic "model
unavailable" message. If structured output validation fails, report the
analysis as unavailable with the exact contract error; never relabel
output-format failure as insufficient evidence.

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
attachments. It requires Workspace `operator` permission and calls the same
normalization and persistence services as Kafka. System administrators cannot
read or operate investigations through the Workbench API.
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
registry, never by an investigation-core product branch. The configurable kinds
are `loki`, `elasticsearch`, `opensearch`, `postgresql`, `mysql`, and `https`;
each kind declares one native
language and its own verification, introspection, parser/policy versions, read
capabilities, adapter, fixture corpus, and failure semantics. A
new provider is not active merely because it resembles an existing product: it
must register a complete independent security profile and contract tests.
The isolated command runner remains an internal execution component and is not
exposed as a user-configurable evidence connector.

Database connectors support PostgreSQL and MySQL through structured host,
port, database, username, mandatory TLS, encrypted password, qualified
allowed-table catalog, AST-validated model candidates, and server-injected
predicates and budgets. The model never receives credentials or direct database
access, and an effective action can execute only through a one-use permit.
Kafka evidence connectors are independent of `Workspace.ingestion_topic` and
are limited to administrator-allowlisted topics and consumer groups.

All user-managed secrets are submitted as values, encrypted immediately with a
data-encryption key derived from `LODE_MASTER_KEY`, stored separately from non-secret config, and never
returned. A Connector secret plaintext is a strict duplicate-free JSON object
of string keys to string values; adapters receive only that frozen decrypted
map. Indirect environment-reference syntax is prohibited for integrations, AI
keys, and Git credentials. `LODE_MASTER_KEY` derives independent JWT,
data-encryption, evidence-authorization, and credential-identity keys using
HKDF domain separation. The master key and configured command-runner key must
each contain at least 32 bytes and differ; startup rejects missing, short, or
reused values. Only token hashes are persisted.

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

The context package always retains normalized input and error structure. Evidence is ordered by causal relevance instead of a simple first-N slice. Exact tokenizer counts are verified before provider calls. Pinned evidence must fit or the route fails; optional evidence may be replaced once by a validated `ContextSummaryArtifact`, but summaries remain derived context and never become independent evidence. Counter-evidence and source mismatches are pinned through compaction.

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

`GET /investigations/{id}` returns a summary-first investigation overview:
input metadata, report narrative, typed timeline, safe evidence index, counts,
language, and archive state. `GET /investigations/{id}/technical` is the
explicit masked technical snapshot for source authority, routing/context, full
operations, and raw report structure. Secret ciphertext, authorization token
hashes, and connector secret values are never serialized.

`POST /investigations/{id}/retry` is valid only for a non-archived terminal
investigation. It creates a new investigation from the immutable normalized
input, restores the sealed opaque trace, and records `retry_of`; it never
mutates or reuses the old run. `POST /admin/investigations/{id}/archive`
requires the system administrator, is valid only for a terminal run, and
permanently makes that run read-only. Archived runs remain available to ordinary
authorized users through detail, event, SSE, and audit reads.

`GET /investigations/{id}/audit` cursor-pages one selected native candidate,
access decision, authorized-read, attempt, or AI-invocation audit chain without
returning encrypted actions or token hashes. The technical endpoint exposes
masked operation purpose, input, progress, result, timing, failure, metrics,
events, and evidence references when explicitly requested.

SSE replays persisted canonical operation event names, including
`operation.started`, `operation.progress`, and `operation.finished`, by their
monotonically increasing sequence. A terminal stream emits
`investigation.finished`. Both the `after` cursor and `Last-Event-ID` are
accepted; the greater cursor wins. Clients use SSE only as an invalidation
channel and reload canonical state from the detail API. A transient disconnect
reconnects with the last observed cursor and preserves the last canonical view.

## Database

The project has not released its database baseline.
`alembic/versions/0001_initial.py` is the only revision and creates exactly the
72 final business tables. There is one current schema and no parallel version,
compatibility view, dual write, backfill, or old-schema adapter; unreleased
development databases are recreated from the unique initial migration. After
the first release, schema changes use ordinary forward migrations.

The table inventory and database invariants are frozen independently under
`contracts/v1/database`. SQLAlchemy metadata must exactly match the migration.
The schema trigger inventory is frozen with the initial migration and database
contract. `set_updated_at()` uses
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

Until the first release, model changes are folded into `0001_initial.py`. Verify a fresh schema:

```bash
LODE_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/lode_migration_test \
LODE_MASTER_KEY=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
uv run alembic upgrade head
uv run alembic check
uv run python scripts/check_schema.py
uv run python scripts/check_database_behavior.py
uv run python scripts/check_intake.py
uv run python scripts/check_resource_graph.py
uv run python scripts/check_evidence_access.py
make log-connectors-check
make native-connectors-check
make investigation-check
make analysis-check
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
shrinkage, unsupported SQL-node rejection, disabled-Connector rejection, duplicate
fingerprint rejection, encrypted-only effective actions, signed hash-bound
tokens, forged permit rejection, one adapter call under concurrent replay, and
terminal immutable success and provider-rate-limit attempts.

`make log-connectors-check` runs the fixed LogQL CST, Elasticsearch/OpenSearch
JSON policy, provider request/response fixture, version isolation,
budgeted introspection, stable pagination, partial response, timeout/rate/auth/5xx,
ValueRef injection, aggregation cost, masking, prompt-injection marking,
registry and forged-permit tests. Run `make install` first; it
performs both `uv sync --all-extras` and the locked, script-disabled npm install
for `tools/logql_parser`. The backend Docker image builds that parser under
Node 24 and copies only its runtime plus the Node executable into the Python
image. The Python image also installs the `git` runtime required by the exact-
revision Git source reader.

`make native-connectors-check` additionally runs the PostgreSQL/MySQL AST and
replica/grant-attestation suites, generic HTTPS canonicalization/SSRF and
endpoint-schema corpus, command argv/path corpus, signed runner protocol,
replay and Connector lifecycle checks, exact-file bubblewrap mapping, high-risk
secret hashing, and Compose privilege/network/key-ownership assertions.

## Frontend Contract

Global admins manage write-only explicit-protocol model account credentials and
reviewed account-model selections at `/[locale]/admin/models`; the UI supports
OpenAI Responses, OpenAI Chat Completions, and Anthropic Messages BaseURLs but
never organization/project IDs, dynamic discovery, or manual model IDs.
Workspace creation atomically requires name and the globally unique Kafka topic.
`/[locale]/admin/git` manages reusable GitHub, GitLab, and Gitee token accounts
and repository-catalogue refreshes. It does not manage Git services, OAuth, or
GitHub App credentials.
`admin/workspaces/[id]` provides Overview, Model policy, Repositories,
Connectors, and Members tabs. The sole system administrator creates ordinary
users and grants each Workspace `viewer` or `operator` access; it also grants a
Git account to a Workspace and selects its repository access. There are no
Workspace administrators. The Repositories tab presents Workspace-derived build units
and components, not raw resource-graph payloads. Connector forms use ordinary
provider-specific fields and require verification before introspection; secrets
are password inputs and are never rendered after submission. Workspace
The system administrator manages bindings, immutable model-policy revisions,
read-only repositories, connector instances, and ingestion transitions.

Authentication uses normalized lowercase usernames. The initial migration
creates exactly one system administrator, `admin`, with password `123456` and
requires an immediate password change. This account is database-protected from
deletion, disablement, renaming, or demotion. Every other account is an ordinary
Workbench-only user: it cannot enter `/admin` or call management APIs. The
administrator has unrestricted access to both the control-plane and Workbench
APIs, including all Workspace resources and investigations. Ordinary users
only see Workspaces explicitly granted to them and remain limited by their
`viewer` or `operator` permission.
Regular accounts are created directly with a one-time initial password, retain
their Workspace grants when disabled, and regain them only after re-enablement.
`invites`, email login, global role assignment, and Workspace `admin`/`analyze`
permissions do not exist.

The only investigation UI lives under `workbench`. Its list supports search,
state filtering, manual intake, and navigation by opaque public ID. Detail leads
with the incident summary, cause, code diagnosis, confirmed facts, evidence gaps,
and recommended next step; it then presents human-readable timeline, evidence,
and execution-audit views. Raw source/runtime/model snapshots remain available
only through the explicit technical view. Terminal runs expose retry and archive
actions according to backend permission and lifecycle rules. The SSE client
reconnects with its last canonical cursor and never translates a historical
response shape.

Wide operational tables scroll inside their own container. Long identifiers
wrap within table cells; tab lists scroll locally at narrow widths. The shell
uses a mobile navigation dialog, and browser checks cover 1440px desktop, 768px
tablet, and 390px phone widths without page-level overflow or occlusion.

## Development And Verification

Backend:

```bash
make install
export LODE_MASTER_KEY='replace-with-a-random-secret-at-least-32-bytes'
export LODE_COMMAND_RUNNER_KEY='replace-with-a-runner-only-secret-at-least-32-bytes'
make local-release-check
```

`make local-release-check` is the complete deterministic gate and must run once
against a fresh isolated upgraded PostgreSQL database. The external statistical
gate is:

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

Web (or run `make web-check` from the repository root):

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
expiry/replay, fingerprint dedupe, Connector lifecycle changes, forged execution permits,
preflight/execution/cancellation terminals, output bounds, and immutable audit.

Log Connector changes must additionally cover complete LogQL CST parsing and
parser differential inputs, root selector enforcement, exact string-node
ValueRef binding, bounded log and metric queries, exact index and field scope,
recursive Query DSL and aggregation allowlists, bucket/cardinality limits,
independent Elasticsearch/OpenSearch version proof, schema introspection,
provider request snapshots, stable pagination/order, partial and malformed
responses, timeout/429/5xx/auth classification, redirect/byte controls, secret
masking, prompt-injection marking, and provider-neutral core
imports.

SQL/HTTPS/Command changes must additionally cover PostgreSQL and MySQL dialect
AST differentials, safe CTE and non-executing EXPLAIN, write/locking/function/
system-catalog rejection, replica and grant attestation, read-only transaction
and cost budgets, canonical URL/SSRF/DNS/redirect/decompression controls, exact
endpoint schemas, argv and ValueRef injection, binary attestation, symlink/path
escape, signed protocol replay, exact-file read-only mounts, empty environment,
private network and secret ownership, Connector lifecycle checks, output truncation,
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

Source/model/report changes must additionally cover exact-SHA resolution with no
default-branch fallback, ambiguous multi-repository matches, credential/revision
drift, source/config authority matrices, strict source artifact provenance,
latency/reasoning routing, role and provider/deployment isolation, per-binding
budgets, tokenizer boundaries, pinned-context overflow, compaction reference and
literal drift, replay without another provider call, verifier disagreement,
immutable report retry, prompt/evidence injection, deterministic gold cases,
false-confirmed metrics, and Wilson confidence release gates.

Before declaring work complete, assess architecture, dependency, and development-workflow impact. Update this file immediately when any of those contracts change, then verify the documented commands and behavior match the implementation.
