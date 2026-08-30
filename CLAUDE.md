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
full backend run is 492 tests. The complete gate includes deterministic fuzz,
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
- The current ORM registry and migration chain register exactly the 73 tables in
  `contracts/v1/database/tables.json`. Provider accounts, account models,
  static Git adapters/accounts/catalogue and account-to-repository access,
  direct Workspace repository bindings, build units, components, resource graph
  revisions, durable repository-analysis jobs and their persisted safe diagnostics, structured Workspace architecture
  context revisions, connectors, immutable investigation snapshots, the evidence graph,
  native-read audit chain, source assessments, findings, and reports are
  separate final objects. Deprecated global workload identity, single-model,
  per-repository Git credentials, and product-specific integration tables and
  routes are not registered.
- Workspace ingestion state is database-shaped rather than API-conventional:
  draft rows have no activation fields, while active/paused rows require a
  positive generation, a frozen start position and activation kind, and
  coherent lifecycle timestamps. Migration `0004_workspace_ingestion_state`
  fail-closes incomplete legacy active rows back to draft without deleting
  their audit history.
- Every generated business-entity primary key uses PostgreSQL `next_lode_id()`,
  a single-node compact snowflake allocator with the `2020-01-01 UTC` epoch,
  42 millisecond bits, and 10 sequence bits. A session advisory lock and
  database clock coordinate API, worker, and consumer processes; rollback is
  clamped and sequence overflow waits for the next millisecond. Entity IDs are
  positive, 10-16 digit JavaScript-safe integers no greater than `2^52-1`.
  `platform_settings.id=1`, revisions, ordinals, and event cursors retain their
  separate meanings. Investigations have no UUID/public-ID alias; routes and
  nullable entity pagination cursors use the snowflake ID directly.
- Git adapters are static reviewed code registrations. Token-only Git accounts,
  encrypted immutable credential revisions, and discovered repository facts are
  global reusable objects. A global admin binds a repository directly by first
  selecting a healthy account and then one repository currently visible to that
  account. `workspace_repository_bindings` stores both IDs and has a composite
  foreign key to `git_account_repository_access`; one repository has at most one
  active binding per Workspace. A binding either follows the discovered repository
  default branch or fixes one verified remote branch; branches are provider-read,
  pageable/searchable values and are never accepted as arbitrary refs, tags, or
  SHAs. Repository and Git-account identity cannot be edited in place. Binding
  edits and soft disable/restore use optimistic revisions; analysis mode,
  alert-source selection, branch, and
  enablement changes make the current analysis stale, while priority and description
  remain metadata-only. Investigation and analysis snapshots resolve the
  binding's exact account and current credential revision and fail closed on
  unhealthy accounts, lost access, archived repositories, or credential drift.
  Private repositories are never accessed through a per-repository secret.
  GitHub, GitLab, and Gitee account tokens are verified
  before their repository catalogues are used. GitHub Enterprise Server and
  GitLab Self-Managed may override the API root; Gitee is official-endpoint only.
  Repository bindings have no service/library/infrastructure role. Their only
  semantic controls are `analysis_mode` (`code` or `documentation`) and
  `is_alert_source`; the latter is valid only for code. A Workspace that can
  start Kafka ingestion has exactly one active alert-source binding. Service,
  gateway, Worker, library, and infrastructure identity comes from scanned Build
  Units, Components, and aliases rather than operator-assigned repository roles.
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
  job. The same transaction archives exactly one succeeded input collection and
  one canonical masked `incident_input` evidence artifact for every new
  investigation; the artifact contains the complete normalized incident while
  secret values remain ValueRef sentinels. `src/lode/consumer/main.py` commits
  Kafka offsets only after that
  transaction succeeds. The consumer uses an exact escaped pattern over the
  active topic inventory, so metadata discovery cannot auto-create a missing
  Workspace topic. A partition-initialization error remains visible until a
  later successful initialization of the same activation generation. Manual
  `POST /investigations` uses the same service.
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
- `src/lode/infrastructure/repository_analysis.py` owns the durable, leased
  repository-analysis workflow. An operator explicitly starts analysis for the
  current active bindings; the job stores an immutable hash-bound binding input
  snapshot (binding/account/repository identity, `analysis_mode`,
  `is_alert_source`, effective branch, and
  structural configuration revision). The worker always uses that snapshot, while rechecking current
  credential and repository access safety, resolves the selected branch to a full
  SHA, reads disposable exact-revision checkouts, publishes the ResourceGraph, and
  records actual branches, source revisions, scan counts, stable failure codes, and
  persisted path/rule/safe-summary diagnostics. A completed analysis is current only
  when its input hash matches the active binding configuration; stale graph revisions
  remain audit history and are not frozen into new investigations. A crash may reclaim
  the lease and safely reuse an identical graph.
- Repository discovery skips symbolic links and records local malformed or unsupported
  manifests as warnings so valid structures still publish. Repository file-count,
  directory-depth, manifest-size, and structured-document safety limits fail the job
  with distinct stable codes. Diagnostics never include manifest contents, raw parser
  exceptions, credentials, or tokens. `PyYAML` is a direct runtime dependency solely
  for safe YAML parsing; JSON, TOML, and XML use standard-library structured parsers.
- Build Units are created for every `analysis_mode=code` binding. Documentation
  bindings provide bounded context but never create executable Build Units. Semantic
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
  Every scope and snapshot uses the closed canonical execution-budget fields
  `max_result_limit`, `max_timeout_ms`, `max_output_bytes`,
  `max_total_output_bytes`, `max_native_reads`, `max_window_seconds`,
  `max_parallel_operations`, and `estimated_cost`; control-plane names are not
  translated at runtime. SQL scope ownership is equally explicit: PostgreSQL
  owns only the `postgres` dialect, MySQL owns only `mysql`, and non-SQL
  Connectors cannot advertise the SQL language.
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
  fail closed. A versioned Loki root filter accepts a maximum-depth-three
  recursive `ALL`/`ANY` tree with `equals`, `not_equals`, `any_of`, and
  `not_any_of`; it normalizes deterministically to at most eight DNF branches,
  32 conditions, and 20 values per set. Every branch requires a positive exact
  matcher. Only server code escapes set values into regex matchers. Branches
  execute independently under one shared budget, then deduplicate and sort;
  any branch failure rejects the complete read. Exact ValueRef string nodes are
  reparsed after binding. The first LogQL query using `incident.trace_id`
  replaces the model selector with the complete Connector root selector; it
  cannot narrow discovery to one app. Archival records the full root DNF,
  allowed and returned apps, scopes without hits, truncation, and record count,
  emits normalized `ObservedEvent` business attributes, and creates a
  server-generated `sealed_trace_correlation` assertion. The trace plaintext
  and enumerable hash never enter ordinary JSON, model context, logs, API, or
  reports. Later component-scoped queries require prior evidence identifying
  that component and create their own scoped correlation assertion. Third-party HTTP evidence Connectors accept canonical
  HTTP or HTTPS origins, including authenticated private-network deployments;
  redirects and URL-embedded credentials remain disabled.
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
  accepts a primary or replica only when its explicit read-only transaction is
  honored and the identity is non-privileged; scope discovery rejects table,
  column, sequence, or Schema-creation write authority in every allowed Schema.
  A physical read replica is recommended but is not a creation requirement.
  MySQL must attest both read-only flags and an exact SELECT/SHOW VIEW grant set. Both require an
  explicit non-fallback TLS mode: `verify_full` is the UI default and verifies
  CA plus hostname; `require` enforces TLS 1.2+ but explicitly does not verify
  server identity. An optional bounded per-Connector CA PEM extends the system
  roots only for `verify_full`; private keys, plaintext, and `prefer` controls
  are rejected. New PostgreSQL Connectors require an explicit
  one-to-32 exact non-system Schema allowlist; discovery parameterizes that
  allowlist, rejects any inaccessible Schema, and considers only readable tables
  within it. MySQL remains bounded to its configured database. A table is safe
  only when it has a non-null temporal
  column and a primary key or all-non-null unique index; time/stable-key choice
  is deterministic, exclusions carry reason codes, and more than 200 candidate
  tables fails instead of truncating. PostgreSQL catalog discovery uses four
  fixed, parameterized batch queries rather than per-table round trips; SQL
  creation and refresh use one 10-second wall-clock discovery budget while other
  Connector kinds retain five seconds. Execution uses explicit read-only
  transactions and one wall-clock deadline per verification, discovery,
  planning, or read operation. PostgreSQL maps known authentication, database,
  TLS, capacity, permission, timeout, and read-only-attestation failures to
  code-authored actionable messages; raw driver errors remain private.
- Generic HTTP(S) accepts only cataloged GET/HEAD endpoints with canonical HTTP
  or HTTPS origins, exact schemes/ports and typed path/query schemas. It has no
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
  `0001_initial` and forward migrations.
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
- Intake now freezes repository policy, connector instances/scopes, eligible
  model binding revisions, model policy, context policy, architecture context,
  and graph identity inside the same transaction as the investigation and job.
  Repository snapshots retain the exact URL, read-only credential identity,
  selected branch, analysis mode, alert-source marker, revision policy, frozen
  SHA when available, and authority status. Kafka `source_revision` is frozen
  as authoritative only on the alert-source repository without a synchronous
  remote Git check. Every other repository starts with `bound_branch_head`
  pending and freezes that branch HEAD on its first source read. Model policy
  `eligible_bindings` contains explicit `{binding_id, revision}` objects; an ID
  or revision mismatch fails closed instead of selecting current control-plane
  state.
- `src/lode/infrastructure/git_source.py`, `source_executor.py`, and
  `source_store.py` provide shell-free, bounded, disposable exact-revision source
  reads. A structured `source_query` carries bounded exact terms, symbols, path
  hints, and source evidence IDs; every value must occur in cited evidence,
  stack frames, or verified component/repository descriptions. Runtime evidence
  that identifies a repository deterministically rejects a different repository
  selection. The alert SHA remains authoritative for the alert source, while a
  non-alert repository's frozen bound-branch HEAD is authoritative by Workspace
  contract. Only an explicit runtime SHA conflict or absence of cited exact
  path/symbol anchors produces `source_snapshot_incompatible`; lack of a generic
  deployment-version proof does not. Archived source artifacts retain repository
  snapshot, SHA, revision origin, path, symbol, line range, masking, and authority/
  compatibility assessment provenance. Source authority constrains only code
  findings for the same repository.
- `src/lode/application/model_routing.py`, `context.py`, and
  `context_compaction.py` plus `src/lode/infrastructure/model_runtime.py` own
  frozen role/execution-class routing, per-investigation and per-binding call/
  cost limits, `tiktoken` counting of the complete serialized OpenAI request,
  immutable context bundles, audited
  replay, and one bounded compaction retry. Canonical `incident_input` evidence
  is mandatory pinned context for planner, synthesis, and verification regardless
  of optional Workspace pin configuration; pinned counter-evidence is also never
  tail-truncated. Compaction rejects reference, number, timestamp, SHA, or
  identity drift; hidden reasoning, raw provider output, sessions, and provider
  caches never cross role/model boundaries.
- `src/lode/structured_output.py` owns the single provider-facing structured-output
  contract. Every response schema is validated before network I/O: every object is
  closed, every property is required, defaults and unconstrained values are rejected,
  and nullable fields remain required. Provider-open documents are represented by an
  explicit bounded duplicate-free JSON string and decoded only at the application
  boundary. Planner decisions use the v5 internal wire schema, which requires one
  to 20 hypotheses and contains only investigation hypotheses and operation intent;
  provider payloads and native-read candidates are forbidden planner fields. The
  separate operation-bound `native-query.v1` protocol returns only one bounded
  provider-payload JSON document. The server derives ValueRef bindings and owns the
  action, Connector, language, purpose, anchors, window, limit, and timeout before
  constructing the strict native-read candidate. Synthesis reports and context
  summaries use v2. DTO or domain
  validation failures become the single controlled `invalid_structured_output`
  result; no older model-output alias or runtime schema rewrite exists.
- Planner, native-query generator, synthesizer, verifier, and context compactor are separate audited
  invocations. Simple tasks route to eligible latency account models; conflict,
  multi-component/repository, deep causal, synthesis, and verification tasks
  require reasoning account models. A route with no eligible frozen candidate is
  persisted with every exclusion and zero capacity before returning unavailable.
  Provider/account-model drift cannot silently admit a replacement model. Model-visible
  data classes are the closed `masked`, `source_code`, `internal`, and `restricted`
  values; binding requests reject unknown values, and route exclusions retain the
  requested and allowed classes.
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
  A missing frozen model policy is an expected `model_capability_unavailable`
  terminal result with an unavailable report, not an unhandled worker failure.
  Invalid planner output is likewise an expected `invalid_structured_output`
  terminal result and cannot escape as a domain validation worker crash.
  A native read is generated only after its durable operation exists. Its
  `native_query` invocation stores that exact `operation_id`; authorization
  rejects planner invocations, unbound invocations, and cross-operation reuse.
  Native-language generation contracts are derived from the exact frozen scope
  and schema snapshot. They expose effective labels, fields, indices, tables,
  endpoints, working-set IDs, grammar, and forbidden constructs without copying
  the complete schema catalog into the prompt a second time.
  Investigation jobs persist separate `investigation` and `reporting` phases.
  Analysis termination moves the public investigation to `reporting` while
  retaining the worker lease; only the report publication transaction moves it
  to `completed` and sets `finished_at`. A crash during reporting resumes that
  phase without re-running the planner or evidence operations, and a job cannot
  complete until its immutable report exists.

- Repository scanner keys and observation refs use the single canonical
  `repository:<binding-id>/...` namespace constructed by
  `repository_candidate_namespace`. Repository analysis, verification scripts,
  and graph persistence consume that constructor instead of defining prefixes
  independently.

Run `make contracts` (or `uv run python scripts/check_contracts.py`) whenever
a frozen contract or evaluation fixture changes. Run
`make schema-check` whenever ORM or migration definitions change. Run
`make intake-check` whenever intake, encryption, idempotency, or replay changes.
Run `make resource-check` whenever scanning, identity validation, graph
publication, derived-resource views, or investigation graph snapshots change.
Run `make evidence-access-check` whenever candidate validation, policy,
ValueRef binding, authorization, execution permits, audit, or Connector
lifecycle checks change. Database-writing verification targets and every pytest
invocation launched by Make run inside a freshly migrated temporary PostgreSQL
cluster on a private Unix socket. Direct pytest and write-capable check scripts
fail closed unless `LODE_TOOLING_ISOLATED_DATABASE=1` and
`LODE_DATABASE_URL` names a `lode_test_*` database. Verification fixtures must
never share the API/consumer/worker database or its global queues.
Run `make log-connectors-check` whenever LogQL parsing, search JSON policy,
provider config/version/introspection, HTTP serialization, pagination,
normalization, masking, or provider failure classification changes.
Run `make native-connectors-check` whenever any native parser/policy, SQL or
HTTPS adapter, command runner protocol/sandbox, connector registry, or native
deployment boundary changes.
Run `make investigation-check` whenever capability construction, decision
policy, dynamic waves, connector snapshots, graph projection/persistence,
operation replay, or worker leases change. Global queue checks run only in the
isolated tooling database.
Run `make analysis-check` whenever repository resolution/source archival, model
policy/binding snapshots, routing, tokenizer/context assembly, compaction,
planner roles, synthesis/verification, authority gates, or report publication
changes. It runs the deterministic quality smoke suite and a repeatable real-
database execution checker, including every production strict response schema.
Run `make api-check` whenever account-model protocol probing or Workspace model
readiness changes; it includes the representative strict-probe and response-validation
tests. `make provider-release-check` is the strict
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
The investigation single-page execution Workbench uses the direct Web dependency
`@xyflow/react` (declared as `^12.11.3`) for read-only pan, zoom, fit-view, and
keyboard-accessible graph navigation. Its three viewer endpoints provide the
canonical graph, structured node detail, and bounded artifact pages. The former
technical snapshot and audit-list endpoints are removed; evidence and simplified
execution records belong to their producing nodes. This changes the API surface
and Workbench information architecture, but adds no dependency beyond React Flow,
does not change investigation execution or persistence, and requires no database
migration.
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
protocol accounts with reviewed catalog-backed account models and a representative
strict structured-output health probe covering required, nullable, nested, array,
and enum behavior. A probe is healthy only when its returned payload also validates
locally. It
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
  authorization, manual intake, summary-first investigation detail, structured
  execution-graph projection and node results, and SSE.
- `src/lode/consumer`: strict Kafka `incident.alert.v1` validation and shared intake dispatch.
- `src/lode/worker`: independent durable investigation and repository-analysis job claiming, leases, retry/recovery, and bounded cross-investigation concurrency.
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
- `src/lode/evidence_connectors/postgresql.py`: transaction/role/grant-attested PostgreSQL read adapter.
- `src/lode/evidence_connectors/mysql.py`: topology/grant-attested MySQL read adapter.
- `src/lode/evidence_connectors/https.py`: generic cataloged GET/HEAD adapter.
- `src/lode/evidence_connectors/command.py`: signed worker client for the isolated runner.
- `src/lode/command_runner`: replay-protected protocol, fixed executor, and minimal FastAPI process.
- `src/lode/evidence_connectors/transport.py`: redirect-free, byte-bounded HTTPS transport.
- `tools/logql_parser/parser.mjs`: credential-free Grafana Lezer LogQL CST helper.
- `src/lode/application/capabilities.py`: minimal frozen capability catalog and credential-free model view.
- `src/lode/application/decision_policy.py`: deterministic relevance, dependency, duplicate, resource, counter-evidence, and budget gates.
- `src/lode/application/investigation.py`: provider-neutral serial-wave orchestration ports and four-operation sibling isolation.
- `src/lode/application/investigation_limits.py`: code-owned hard investigation
  ceilings frozen into each investigation; users and models cannot configure or
  enlarge them.
- `src/lode/application/evidence_graph.py`: deterministic timeline, entity matching, observations, and evidence-backed causal projection.
- `src/lode/infrastructure/investigation_snapshots.py`: immutable active/healthy connector capability freezing.
- `src/lode/infrastructure/investigation_store.py`: decision, step, operation, event, budget, and replay persistence.
- `src/lode/infrastructure/investigation_leases.py`: skip-locked job claims, heartbeat, retry, and expired-owner recovery.
- `src/lode/infrastructure/evidence_graph_store.py`: ownership-checked idempotent graph persistence.
- `src/lode/infrastructure/evidence_archive.py`: normalized native-result collection/artifact archival.
- `src/lode/application/native_query.py`: minimal native-query DTO, canonical
  ValueRef sentinel derivation, and server-owned candidate assembly.
- `src/lode/infrastructure/native_query.py`: operation-bound audited native-query
  invocation over frozen Connector, model-policy, evidence, and budget snapshots.
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
  plus the investigation Workbench for manual intake, summary-first single-page
  diagnosis, compact/full execution flow, structured node detail, retry,
  archive, and SSE refresh. All user-visible product text is served through
  `next-intl`.

PostgreSQL is the source of truth. Kafka consumers only validate and enqueue;
workers execute investigations. FastAPI exposes health, authentication,
system-administrator user and Workspace-member administration, global
provider-account/model administration, Workspace policy/repository/connector
control, and a separate ordinary-user Workbench API for manual intake,
investigation, execution-graph, retry, and SSE reads. The singleton
`platform_settings` row selects the output language for newly created
investigations; each investigation freezes that language. Model routing is frozen per
investigation from its Workspace policy and eligible account models; there is no
single-model or global-default fallback.

## Workspace Activation

The Workspace Overview is the single operational surface for readiness,
start/pause/resume, and desired-versus-observed listener state. `GET
/workspaces/{workspace_id}/readiness` returns typed `passed`, `warning`, and
`blocked` checks plus consumer identity, assigned partitions, heartbeat, and
error state. Every transition into active Kafka ingestion must fail closed
unless all four blocking conditions hold:

1. the Workspace has its required globally unique ingestion topic;
2. its active model policy can route every required role to an active,
   protocol-healthy account model;
3. the broker can reach the configured topic;
4. exactly one active `analysis_mode=code` repository is marked
   `is_alert_source=true`.

Initial start and resume call the same backend readiness gate. Current
repository analysis, healthy evidence connectors, and non-empty architecture
context are explicit warnings; they do not block alert ingestion. A missing or
ambiguous alert source is a repository blocker.
Model readiness requires every mandatory model role to have a healthy current
binding that accepts the baseline `masked` data class used by the first planner
decision; role-only coverage cannot make a Workspace ready.
The globally unique topic is editable only while ingestion is `draft` or
`paused`. Changing it resets ingestion to `draft`, clears the prior start
position and listener timestamps, and requires a fresh `earliest`/`latest`
start after readiness passes. A `start` activation ignores historical Kafka
consumer-group commits and freezes explicit per-partition targets; `resume`
continues from committed offsets. Rebalances within the same activation
generation never rewind an initialized partition. Active topic changes return
HTTP 409 `ingestion_topic_change_requires_pause`, and the consumer drops the old
topic on its next active-subscription refresh.
Missing requirements return HTTP 409 using the canonical business-error
envelope:

```json
{
  "error": {
    "code": "workspace_not_ready",
    "message": "Complete all required Workspace settings before starting ingestion.",
    "details": {
      "blockers": [
        {"code": "model_policy", "details": {"missing_roles": ["planner"]}}
      ]
    }
  }
}
```

The Web displays the actual checks inline and keeps the start action visible. An
account model must pass its provider protocol probe before becoming routing
eligible; editing provider, protocol, Base URL, API Key, or model selection
clears incompatible discovery state and resets health to `untested`. The
backend gate remains authoritative for stale clients and direct API callers.

## Investigation Execution

An investigation owns exactly one active decision wave:

1. Canonicalize and mask the complete error input, then archive it as the
   mandatory pinned `incident_input` evidence artifact in the intake transaction.
2. Parse stack frames and the structured error contract.
3. Reload committed evidence, hypotheses, completed fingerprints, and remaining
   server budget.
4. Use the repository, Connector, scope, model binding/policy, context, and graph
   snapshots frozen atomically at intake; build a minimal credential-free server
   action catalog.
5. Ask the planner to finish or select one to four independent operation intents;
   planner output never contains a provider payload or native-read candidate.
6. Apply deterministic relevance, dependency, duplicate,
   resource-conflict, counter-evidence, and server-budget policy. One structured
   repair is allowed after rejection.
7. Persist operation IDs and execute the allowed wave with at most four sibling
   operations.
8. For each native read, invoke the `native_query` role with the persisted
   operation ID and frozen Connector context. Accept only its bounded provider
   payload, then derive ValueRef bindings and assemble the candidate envelope,
   window, limit, and timeout on the server.
9. Authorize and execute the assembled candidate through the Evidence Access
   kernel; dispatch uses the persisted server-owned operation kind, never model data.
10. Persist every operation result and evidence artifact independently; one
   failed operation does not cancel its siblings.
11. Project standard events, entities, relations, observations, and a stable
   timeline only from archived evidence. Shared trace membership alone never
   creates direction.
12. Start the next decision only from evidence committed by the prior wave.

Parallelism is allowed only inside an explicit wave and must remain at or below four operations. Allocate operation IDs/ordinals before launching work, use `return_exceptions=True`, and persist each result separately. Do not overlap decision waves. `LODE_WORKER_CONCURRENCY` separately controls how many investigations workers may run.

Investigation depth is a runtime decision, not Workspace configuration. After
every committed wave the planner receives the input, current hypotheses,
archived evidence, conflicts, gaps, and remaining budget, then chooses
`finish` or one to four next operations. The service owns immutable ceilings of
16 decision waves, 14 model calls, 12 native reads, 16 MiB output, cost 200,
900 wall-clock seconds, a 30-minute incident window on each side, and four
parallel operations per wave. The complete ceiling and exact time window are
frozen into each new investigation. Retry inherits the parent's exact budget
and window but starts with independent usage. The runtime clamps stored values
to the code ceiling and terminates as `investigation_budget_exhausted`; neither
environment variables, model-policy JSON, nor model output can enlarge it.

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
Kafka supports `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, and `SASL_SSL` deployment
profiles. `SASL_PLAINTEXT` remains an explicit compatibility mode; remote
deployments should prefer `SASL_SSL` with certificate and hostname verification.
An optional deployment-mounted CA file may extend the system roots. Roles that
do not use Kafka do not validate Kafka transport configuration at startup.

`platform_settings` is a singleton revisioned product setting. Its
`ai_output_language` is restricted to the system-supported language list
(`en`, `zh`); it applies only to new investigations and is frozen into every
investigation and all model role prompts.

Operation ordinal is the durable wave idempotency boundary; `action_id` and
planner-level operation fingerprints are not global duplicate gates. Connector
evidence reuse uses the complete authorized effective-query fingerprint,
including connector snapshot, language, generated query, bound sealed values,
effective window, direction, limit, timeout, and budget. An identical successful
native query reuses its artifacts; a different query against the same Connector
executes normally. Source reuse uses repository snapshot, frozen SHA, normalized
terms, symbols, path hints, and evidence references; only an identical source
query reuses its collection. Steps and operations commit independently. PostgreSQL permits one running
step/decision wave while multiple operations in that wave may run. On worker recovery,
completed evidence is reused and the first unfinished durable action resumes. A second
policy rejection after the single repair terminates as `insufficient`, not model
unavailability. Lease cleanup only changes jobs whose own leases expired and whose
persisted phase matches the investigation state.

Planner output may select catalog IDs and cite archived evidence IDs. Only the
operation-bound native-query role may propose the provider-specific LogQL,
Elasticsearch/OpenSearch Query DSL, SQL, safe HTTPS, or fixed-command payload,
using exact server-published ValueRef sentinels. The service derives the binding
map and all candidate envelope fields; no model role can choose Connector scope,
window, limit, or timeout. Every assembled candidate still
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
probe for every selected model before it becomes routable. The same protocol-native
probe is used by the explicit model-test endpoint; it is not a plain-text completion
check. The only routing
targets are reviewed fixed catalog IDs. Each account model records
`synced`/`manual`/`missing` discovery and
`untested`/`healthy`/`unavailable` protocol health; an upstream disappearance
soft-disables a synced model without removing the audit record, while a manual
model remains until explicit removal or a failed probe. `List models` never
proves completion compatibility: `POST /ai-provider-accounts/{id}/models/{model_id}/test`
uses the account's registered protocol and must succeed before routing can select it.

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

`ingestion_version` is an internal activation generation, not a user-facing
release version. On the first activation generation, the chosen `earliest` or
`latest` position is resolved and frozen per Kafka partition. Rebalances never
rewind behind that target; later resume generations continue from committed
offsets. The consumer continuously writes observed generation, consumer ID,
partition assignment, heartbeat, and error state to
`workspace_ingestion_runtime`.

Manual `POST /investigations` accepts `workspace_id`, timezone-aware
`occurred_at`, `severity`, `event`, optional opaque `trace_id`, optional
lowercase `source_revision`, structured `error`, and at most ten bounded typed
attachments. It requires Workspace `operator` permission and calls the same
normalization and persistence services as Kafka. System administrators cannot
read or operate investigations through the Workbench API.
Removed service/environment/request fields fail strict validation.

`source_revision` is immutable Kafka alert evidence and the authoritative SHA
for the Workspace alert-source repository. It is not a generic runtime commit
for every repository and is never compared with non-alert repository snapshots.

## Source Investigation

Workspace, repository, build unit, component, and runtime resource are separate
identities. A Workspace binds repositories; scanner-produced BuildUnits and
Components plus ResourceGraph observations provide independently sourced
identity. Investigation creation freezes repository, component, resource,
connector-scope, model-policy, architecture-context, and graph-revision
snapshots. Identity is never learned from an inbound service-name header.

The alert `source_revision` is authoritative only for the single alert-source
repository. All other code repositories are equal runtime participants: the
first source operation freezes the configured bound-branch HEAD and treats that
SHA as production-authoritative by Workspace contract. Runtime logs or component
identity evidence establish participation and repository relevance; generic
absence of a deployment SHA is never a gap. Unread and unidentified repositories
do not appear in source assessments or report gaps.

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
optional defaulted port, database, username, mandatory verified TLS,
encrypted password, automatically discovered safe-table scope, AST-validated
model candidates, and server-injected predicates and budgets. New PostgreSQL
Connectors require an explicit exact Schema allowlist stored only in the access
scope; MySQL uses its configured database as the Schema boundary. PostgreSQL
scope revisions without `allowed_schemas` are invalid; there is no historical
range fallback. Operators cannot submit allowed tables, time columns,
stable ordering or `search_path`. The model never receives
credentials or direct database access, and an effective action can execute only
through a one-use permit.
An optional 64 KB CA certificate PEM may extend only that Connector's system
trust store in `verify_full` mode. It is parsed before remote I/O, frozen with
the Connector config, omitted from control-plane responses, and never permits a
private key or hostname mismatch. The explicit `require` mode keeps encryption
mandatory but makes its lack of server-identity verification visible; switching
to it clears and rejects CA input.
Database and account names are bounded driver parameters rather than SQL
identifiers, so provider-valid punctuation such as Supabase Pooler's dotted
PostgreSQL usernames is accepted; surrounding whitespace and control characters
remain invalid. Request-validation responses identify the first invalid field
without echoing submitted values.
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

Workspace `description` is a short operator-facing summary. AI background is
configured separately as bounded, typed architecture-context entries for system
purpose, architecture constraints, critical flows, dependencies, and operational
conventions. At
investigation creation, the current ordered context entries are masked and
frozen as an immutable snapshot. Every model phase receives that snapshot as
explicitly untrusted background: it may clarify boundaries and architecture,
but it cannot override system rules or independently prove an incident cause or
code defect. Later context edits affect only new investigations.

Native connector payloads are generated by an operation-bound `native_query`
invocation from frozen scope, then wrapped only by deterministic server assembly.
AI-generated native payloads can reach a provider only through the
Evidence Access authorization chain. Raw trace values are resolved server-side
from sealed storage only after authorization and are never supplied to the
model. Log provider credentials support exactly one registered authentication
form, are injected after authorization, and never appear in requests, results,
ordinary config, or audit JSON.

Source lookup order is fixed:

1. Use the incident trace for a server-generated Loki root-scope discovery over
   every allowed app and archive its exact correlation assertion.
2. Parse returned app/service/business fields into `ObservedEvent`, then map
   evidence-identified Components and repository aliases.
3. Select the relevant code repository and freeze either its alert SHA or bound
   branch HEAD according to repository policy.
4. Search only bounded exact terms, symbols, and path hints grounded in cited
   evidence, stack frames, or verified component descriptions.
5. Inspect caller/callee, error branches, exception conversion, return checks,
   timeout/retry handling, and related tests in further distinct source queries.

Generated build directories are excluded. Project documentation provides vocabulary and repository context only. A file read, path match, error-code match, or README excerpt is never code-cause proof.

The context package always retains normalized input and error structure. Every
evidence item contains masked content, masked provenance, source time/revision,
scope coverage, and its server assertions. Planner, native query, synthesizer,
and verifier also receive the complete structured server assertion graph,
including assertions without a supporting artifact link. A confirmed
`sealed_trace_correlation` assertion is authoritative for its exact claim; a
model cannot reject the linked Loki records merely because the visible trace or
request ID was redacted, and model output cannot create such an assertion.
Evidence is ordered by causal relevance instead of a simple first-N slice. Exact tokenizer counts are verified before provider calls. Pinned evidence must fit or the route fails; optional evidence may be replaced once by a validated `ContextSummaryArtifact`, but summaries remain derived context and never become independent evidence. Counter-evidence and source mismatches are pinned through compaction.

## Result And Code Contracts

Investigation result states are `pending`, `confirmed`, `hypothesis`, `insufficient`, and `unavailable`. There is no overall confidence number.

- `confirmed` requires an independent semantic verification pass plus at least one
  evidence-backed confirmed incident cause or a confirmed incident-version code
  finding that passes all structural gates. External causes do not require a
  project code finding.
- `hypothesis` contains one supported mechanism, exact candidate code when available, counter-evidence, and a validation method.
- `insufficient` contains no invented cause.
- `unavailable` reports missing required capability or repeated strict-output contract failure without a fabricated fallback conclusion. Output contract failure explicitly states that it does not prove evidence insufficiency.

Non-confirmed reports may request evidence and propose tests. They may not prescribe a production code change.

Reports always separate:

- `incident_cause`: the actual incident mechanism, including an external service, network, data, or infrastructure failure;
- `code_diagnosis`: a project defect, a resilience gap, `no_defect`, or `not_found`.

An `InvestigationCodeFinding` with `confirmed` or `hypothesis` must reference an immutable `source_file` artifact and exactly match its repository ID, full SHA, revision origin, path, symbol, and line bounds. It explains faulty behavior, why it violates a contract, expected behavior, trigger, propagation, incident evidence, supporting evidence, counter-evidence, missing validation, and a test scenario.

For `confirmed`, the revision origin must be `alert_revision`, authoritative
`bound_branch_head`, or independently corroborated `runtime_observed`; the same
repository assessment must not be incompatible. An explicit runtime SHA conflict
or evidence-grounded exact path/symbol incompatibility blocks that code finding.
It does not downgrade an independently verified external-service,
configuration, network, or data incident cause. The independent verifier must
still prove the branch, trigger, and propagation for confirmed code.

External causes can be represented accurately while code diagnosis separately reports no project defect or an exact timeout, retry, validation, fallback, or error-preservation weakness.

## API And Events

HTTP errors use one envelope: `error.code`, a string or HTTP status code; `error.message`, always a string; and optional structured `error.details`. Do not place structured objects in `error.message`.

`GET /investigations/{id}` returns the summary-first single-page overview: input
metadata, counts, language, archive state, and a structured report. Root cause
and code diagnosis each carry status, human-readable summary, causal chain where
applicable, and deterministic artifact IDs; confirmed facts carry their own
artifact IDs. The response has no duplicated timeline, evidence index, raw
report, technical snapshot, or audit-list shape. Secret ciphertext,
authorization token hashes, connector secret values, prompts, full model
context, and hidden reasoning are never serialized.

`GET /investigations/{id}/execution-graph` is the canonical viewer projection
for the read-only Workbench flow. It returns schema version, persisted event
cursor, derived phase, active node IDs, deterministic stages, Connector and
repository lanes, nodes, directed edges, and frozen unused Connectors. Stable
node IDs use `input:`, `decision:`, `operation:`, `synthesis:`, `verification:`,
and `report:` prefixes. A non-persisted planning, reporting, queued, or failure
placeholder may use `phase:` and always has `detail_available=false`. Each real
Connector invocation is one operation node; parallel operations share the same
execution stage and have no edge between them. A running operation takes phase
priority over the job phase. Without one, persisted job phase selects planning
or reporting; terminal investigation status remains final. Every node exposes
an ordered `evidence_refs` list containing only the masked artifact IDs actually
owned by that node; `evidence_count` is derived from the same set.

`GET /investigations/{id}/execution-graph/nodes/{node_id}` returns the selected
node's purpose, selection reason, expected evidence, masked proposed/effective
query, authorization constraints, attempts, metrics, failures, operation events,
artifact index, and first result page. It never exposes provider prompts, full
model context, hidden reasoning, sealed values, credentials, ciphertext,
authorization hashes, or tokens. An allowed query without a persisted attempt
is `authorized`; only a persisted attempt makes it `executed`. A
pre-authorization rejection remains `proposed` and cannot be presented as
executed.

`GET /investigations/{id}/execution-graph/nodes/{node_id}/artifacts/{artifact_id}`
pages immutable masked artifact records by stable zero-based `after_index`. The
requested limit is at most 100 and the complete serialized page, including
metadata, is bounded to 256 KiB. Oversized metadata or one oversized record is
returned as an explicitly truncated preview. Both node and artifact ownership
are revalidated inside the investigation before content is returned. All three
endpoints require Workspace `viewer` permission.

`POST /investigations/{id}/retry` is valid only for a non-archived terminal
investigation. It creates a new investigation from the immutable normalized
input, restores the sealed opaque trace, and records `retry_of`; it never
mutates or reuses the old run. `POST /admin/investigations/{id}/archive`
requires the system administrator, is valid only for a terminal run, and
permanently makes that run read-only. Archived runs remain available to ordinary
authorized users through overview, execution-graph, node detail, event, and SSE
reads. `/investigations/{id}/technical` and `/investigations/{id}/audit` do not
exist; the node-detail projection is the only ordinary viewer path for masked
queries, results, failures, and simplified execution records.

SSE replays persisted canonical operation event names, including
`operation.started`, `operation.progress`, and `operation.finished`, by their
monotonically increasing sequence. A terminal stream emits
`investigation.finished`. Both the `after` cursor and `Last-Event-ID` are
accepted; the greater cursor wins. Clients use SSE only as an invalidation
channel and reload canonical state from the detail API. A transient disconnect
reconnects with the last observed cursor and preserves the last canonical view.

## Database

Database revisions may already be deployed and are immutable once executed.
`alembic/versions/0001_initial.py` creates the original 72-table baseline and
`0002_repository_binding_analysis.py` expands it to the 73-table inventory that
the current forward chain retains. `0003_schema_catalog_secret_scope.py` updates only the
secret-rejection trigger function: ordinary Connector and scope configuration
still rejects credential keys recursively, while server-generated Schema
catalogues may preserve legitimate provider identifiers such as `token` or
`password`. It changes no table, trigger inventory, or stored row.
`0004_workspace_ingestion_state.py` closes the Workspace ingestion lifecycle.
`0005_canonical_evidence_budget.py` publishes immutable canonical scope revisions
and enforces their exact execution-budget shape. `0006_sql_scope_dialect.py`
publishes explicit SQL dialect revisions and adds bidirectional Connector-kind,
language, and dialect enforcement. `0007_investigation_job_phase.py` adds the
durable investigation/reporting job phase and the non-terminal public
`reporting` status. `0008_confirmed_report_semantics.py` aligns the immutable
report trigger with the result contract: a confirmed report requires a successful
investigation verifier plus an owned-evidence incident cause or the exact confirmed
code findings referenced by the report. `0009_confirmed_report_invocation_anchors.py`
makes nested status checks null-safe and requires the report's successful synthesizer,
verifier, and confirmed findings to belong to the same investigation and verification
decision. `0010_evidence_authority.py` is the intentionally destructive forward
replacement for repository roles, source revision authority, operation dedupe,
and report v2 fields. It clears investigations and all derived evidence, report,
source, and ResourceGraph state; removes every Workspace repository binding; and
returns active or paused ingestion to `draft` with a new ingestion version so
consumers stop until readiness passes again. It preserves Workspaces, Git
accounts and repository catalogues, Connectors, and model configuration. No old
role is mapped and there is no dual read, alias, or rollback. It installs
one-time non-alert branch-HEAD snapshot freezing and one-way source-authority
contradiction triggers. Executed migration files remain immutable. There is one
current schema and no compatibility view or dual write.
This architecture/workflow change introduces no new dependency; operators must
rebind repositories under the new analysis-mode/alert-source contract after
upgrade. Future schema changes use ordinary forward migrations.

`next_lode_id()` and its state sequence are created before business tables in
the initial migration. Tests exercise concurrent connections, independent
processes, monotonic allocation, clock rollback, 1024-ID millisecond overflow,
digit length, JavaScript safety, and fresh-schema foreign keys.

The table inventory and database invariants are frozen independently under
`contracts/v1/database`. SQLAlchemy metadata must exactly match the migration.
The schema trigger inventory is frozen by the complete forward migration chain and
database contract. `set_updated_at()` uses
`clock_timestamp()` so updates within one transaction still advance the value.
Ordinary connector/scope JSON is traversed structurally and rejects credential
keys at any object depth. Schema catalogues are excluded from that key-name
heuristic because provider table/field identifiers are metadata rather than
credential values; only server-generated catalog structures are persisted.

Native-read attempts are terminal immutable audit records. An executor inserts
one after the operation finishes, with a non-null `finished_at`; success has no
failure payload, while failure/interruption requires a stable failure code.
Retries insert a new `(authorized_read_id, attempt)` row.

`audit_events` is immutable. Its actor and Workspace foreign keys use
`ON DELETE RESTRICT`, not `SET NULL`, because cascading nullification would
rewrite historical audit identity. Users and Workspaces referenced by audit are
disabled rather than physically deleted.

The supported verification workflow creates, migrates, and destroys its own
PostgreSQL cluster:

```bash
make schema-check
make intake-check
make resource-check
make evidence-access-check
make log-connectors-check
make native-connectors-check
make investigation-check
make analysis-check
make test
```

Alembic autogeneration against that database must produce no schema difference.
Each Make target requires local PostgreSQL server tools (`initdb`, `pg_ctl`, and
`createdb`) and uses `scripts/run_with_isolated_postgres.sh`. The behavior
checker rolls back all fixtures, including its concurrent unique
topic writes. The intake checker verifies strict validation, all dedupe layers,
concurrent races, durable DLQ/unassigned handling, current-validator replay,
manual HTTP intake, and exact encrypted trace round trips. `make resource-check`
verifies single/monorepo and multi-repository
identity, documentation-binding Build Unit exclusion, recovery reuse, alias conflict,
revision invalidation, immutable historical membership, investigation snapshot
freezing, access-boundary preservation, and the authenticated read-only graph
view. `uv run python scripts/seed.py` is idempotent and may only create final
control-plane models.

The evidence-access checker constructs the full snapshot/model/operation/audit
chain through the production Elasticsearch policy. It verifies exact opaque
ValueRef round trips, mandatory timestamp/source/sort constraints, budget
shrinkage, unsupported SQL-node rejection, disabled-Connector rejection, duplicate
in-flight/failed fingerprint rejection, exact successful-query reuse,
different-query execution, encrypted-only effective actions, signed hash-bound
tokens, forged permit rejection, one adapter call under concurrent replay, and
terminal immutable success and provider-rate-limit attempts.

`make log-connectors-check` runs the fixed LogQL CST, Elasticsearch/OpenSearch
JSON policy, provider request/response fixture, version isolation,
budgeted introspection, stable pagination, partial response, timeout/rate/auth/5xx,
ValueRef injection, aggregation cost, masking, prompt-injection marking,
registry and forged-permit tests. It also verifies server-expanded three-app
trace discovery, complete scope coverage, Payssion business-field parsing,
sealed trace assertions, and model packages without trace plaintext or hashes.
Run `make install` first; it
performs both `uv sync --all-extras` and the locked, script-disabled npm install
for `tools/logql_parser`. The backend Docker image builds that parser under
Node 24 and copies only its runtime plus the Node executable into the Python
image. The Python image also installs the `git` runtime required by the exact-
revision Git source reader.

`make native-connectors-check` additionally runs the PostgreSQL/MySQL AST and
read-only transaction/grant-attestation suites, generic HTTPS canonicalization/SSRF and
endpoint-schema corpus, command argv/path corpus, signed runner protocol,
replay and Connector lifecycle checks, exact-file bubblewrap mapping, high-risk
secret hashing, and Compose privilege/network/key-ownership assertions.

## Frontend Contract

Global admins manage write-only provider model accounts at
`/[locale]/admin/models`. Requests use `provider_kind`, `protocol_id`,
`base_url`, `api_key`, and structured model selections. OpenAI exposes only
Responses or Chat Completions; Anthropic exposes only Messages, and switching
provider clears incompatible protocol, Base URL, discovery, and selection
state. The API Key is never returned or placed in errors/logs. Unsaved accounts
can discover models, saved accounts can refresh them, and the server follows the
official bounded OpenAI `/models` and Anthropic `/v1/models` inventories.
Discovered but unreviewed IDs remain visible and disabled. Manual entry accepts
only exact IDs in the reviewed server catalog; arbitrary IDs, aliases, capacity,
and tokenizer parameters are rejected. OpenAI uses the reviewed local tokenizer
strategy, while Anthropic calls its official token-count endpoint before the
Messages request.

The reviewed catalog currently contains OpenAI GPT-5.6 Sol/Terra/Luna and
Anthropic Claude Fable 5, Opus 5, Sonnet 5, and Haiku 4.5, with source URL,
review date, context/output limits, capabilities, protocols, and immutable
profile hash. A disappeared discovered model is marked `missing` and disabled;
a manual selection remains usable only after a successful probe.
Workspace creation atomically requires name and the globally unique Kafka topic,
and creates architecture-context revision 1. The optional Workspace description
and inactive Kafka topic are editable operator metadata.
`/[locale]/admin/git` manages reusable GitHub, GitLab, and Gitee token accounts
and repository-catalogue refreshes. It does not manage Git services, OAuth, or
GitHub App credentials.
`admin/workspaces/[id]` provides Overview, Model policy, Repositories,
Connectors, and Members tabs. Each tab loads through an independent failure
boundary, so a broken integration cannot hide readiness or other healthy
configuration areas. Overview groups the Kafka topic and Workspace description
under one settings action and sends a single Workspace patch; architecture
context uses a separate `Publish new revision` action because each update creates
an immutable revision. Per-field generic Save buttons are not part of this flow.
The Members tab independently loads its own member/user
data, provides combined search plus permission/status filters, and uses compact
rows with initials, status, permission, independent row actions, destructive
confirmation, and a right-side add drawer. The sole system administrator creates
ordinary users and grants each Workspace `viewer` or `operator` access. There
are no Workspace administrators. The Repositories tab selects a searchable
healthy Git account and then a searchable repository within the two combobox
popovers; changing the account clears the repository immediately. It starts and polls durable
repository analysis, shows the exact analyzed branch and commit for every binding,
warning diagnostics, and current/expired status. It presents identified build units
and components only after a successful run, not raw resource-graph payloads or the
internal binding revision. The repository table includes the actual account used by
every binding, `analysis_mode`, and alert-source status plus edit, soft-unbind,
and restore actions. The editor exposes code/documentation selection and one
Workspace-wide alert-source choice; it never offers service/library/
infrastructure repository roles. Unbind confirmation makes
clear that history is retained. There is no separate Workspace Git-account
authorization step or entitlement selector.
Connector forms are one provider-specific page grouped into basic information,
connection information, and read scope. Optional values stay empty and identify
themselves as optional in placeholders. Multi-value inputs preserve editing text,
accept Enter/comma/paste, then trim, drop empty values, and deduplicate. The
primary `Create and verify` action remains enabled outside request processing;
submission reports field errors and focuses the first invalid field.
`POST /workspaces/{id}/evidence-connectors` validates configuration, verifies the
remote identity, and discovers the final scope before opening the persistence
transaction. Only success atomically writes a healthy Connector, encrypted
secret, scope, catalog, and audit event; failure writes none and the Web form
retains its values. Provider failures expose code-owned actionable reasons and
allowlisted structured details (for example observed/supported versions,
failed PostgreSQL read-only checks, or a safe SQLSTATE), never raw exception
text, response bodies, or credentials. The Web failure banner renders those safe
diagnostic identifiers next to the actionable reason. Existing test/introspection
endpoints remain operational refresh actions. Secrets are password inputs and are never rendered after
submission. Loki uses the recursive condition-tree editor. The
system administrator manages bindings, immutable model-policy revisions,
read-only repositories, connector instances, structured architecture context,
and ingestion transitions. Investigation depth has no control-plane field or UI;
the planner decides whether to finish or continue inside the code-owned ceiling.

The authenticated shell follows `apps/web/DESIGN.md`: 256px fixed desktop
sidebar, 64px tablet rail, mobile navigation drawer, 56px context bar, neutral
one-pixel borders, 6px control radii, 8px operational panels, 36px controls,
compact tables, and non-nested operational sections in both themes. Shared shell
fidelity rules live in `apps/web/app/dashboard.css`, imported after the domain
and Tailwind rules in `globals.css`; investigation-specific visualizations stay
in `globals.css`. The sidebar Find command supports mouse and `F` keyboard
navigation while preserving the current permission-filtered route set. Shared
buttons preserve size while loading, set `aria-busy`, and prevent duplicate
submissions; row actions have independent state. Shared text inputs and
textareas force autocomplete/password-manager opt-out attributes. Text inputs
also remain read-only until their first user focus so browsers cannot inject
saved values during page load, then behave as normal controlled inputs. Lists
use structural skeletons on first load, retain prior data during refresh, and
define empty, filtered-empty, inline-error, and retry states. All visible labels,
accessibility names, enum values, placeholders, validation messages, and client
API errors use `next-intl`. `npm run check:i18n` enforces English/Chinese key
parity and scans TSX literals; dates and numbers use the active locale.
The PostgreSQL scope/create-workflow change adds no dependency or kind-version
bump. Its catalog-trigger correction is delivered only through the V3 forward
migration; V1 and V2 remain unchanged. Generic HTTP(S) scope is intentionally strict:
new instances use kind version 2 and every endpoint requires an explicit
`scheme`; there is no legacy scope fallback. It adds no dependency or database
schema migration.
Database TLS-mode/custom-CA support also adds no dependency, migration,
kind-version change, or compatibility path. Persisted configs and every new API
request must explicitly select a mode.
PostgreSQL primary acceptance, scoped write-grant proof, and batched discovery
add no dependency, database migration, kind-version change, or operator-tunable
timeout. They change only verification/discovery policy and its fixed budget.

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
state filtering, manual intake, and navigation by compact entity ID. Detail leads
with the incident summary, cause, code diagnosis, confirmed facts, evidence gaps,
and recommended next step in one continuous page with no page-level tabs. The
read-only execution graph follows the report and defaults to a compact projection
containing input, Connector/source operations, live phase, and result. Full mode
restores decision, synthesis, and verification nodes; compact transitive edges
preserve reachability without inventing order between parallel operations.
Investigation rounds form the horizontal axis and frozen Connector/repository
lanes the vertical axis. Desktop and tablet use React Flow pan, zoom, fit-view,
keyboard focus, and a locate-current-step control. Stage labels and lane bands
are structural graph nodes and share the same pan/zoom transform as execution
nodes and edges. Phones below 768px use the
same projection as a round-grouped vertical list. Selecting a persisted node or
report evidence reference opens the Radix right-side drawer and restores the
previous graph position and focus when closed. SQL uses a dynamic-column table,
Loki a time-ordered log list, search providers structured conditions and result
tables, HTTPS typed request/response fields, Command bounded output lines, and
source reads repository/path/line/code presentation. Unknown values use bounded
key-value, list, or table presenters and never formatted raw JSON. Terminal runs
expose retry and archive actions according to backend permission and lifecycle
rules. The SSE client
starts at the graph's canonical cursor and invalidates overview and graph state
without resetting a manual node selection. While a non-terminal investigation
is visible, a five-second canonical refresh covers planning and reporting
intervals that have no operation event. It never translates a historical
response shape.

Wide operational tables scroll inside their own container. Long identifiers
wrap within table cells. The shell
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

Local release and seed fixtures use the current Git authorization chain:
account, encrypted credential revision, current repository visibility/access,
then a direct Workspace repository binding containing the account and repository
IDs. Verification scripts must preserve that composite access constraint.

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
archive immutability, structured operation detail, SSE replay,
permissions, masking, and fresh schema creation.

Resource-understanding changes must additionally cover single repositories,
nested Node/Python/JVM workspaces, multi-repository Components, documentation
repositories, path/symlink/YAML/XML attacks, bounded structure parsing,
provenance independence, within/cross-publication alias conflicts, annotation
authorization expansion, observation/publication idempotency, invalidation,
immutable graph membership, job recovery, and investigation snapshot isolation.

Evidence-access kernel changes must cover duplicate/oversized/deep/invalid
candidate JSON, every stable rejection class, snapshot ownership, missing or
partial parser behavior, scope and budget intersection, arbitrary ValueRef
strings and injection shapes, authorization key separation, token tamper/
expiry/replay, full authorized-query fingerprint reuse, distinct-query execution,
Connector lifecycle changes, forged execution permits,
preflight/execution/cancellation terminals, output bounds, and immutable audit.
Native-query changes must additionally cover operation-bound invocation ownership,
unknown or transformed sentinels, server-derived bindings/window/limit/timeout,
strict payload-only output, and rejection of removed planner candidate fields.

Log Connector changes must additionally cover complete LogQL CST parsing and
parser differential inputs, nested filter normalization, DNF complexity and
positive-matcher limits, server-only regex escaping, root selector enforcement,
branch budget sharing, stable merge/deduplication, atomic partial failure, exact
string-node ValueRef binding, bounded log and metric queries, exact index and field scope,
recursive Query DSL and aggregation allowlists, bucket/cardinality limits,
independent Elasticsearch/OpenSearch version proof, schema introspection,
provider request snapshots, stable pagination/order, partial and malformed
responses, timeout/429/5xx/auth classification, redirect/byte controls, secret
masking, prompt-injection marking, first-trace root-scope replacement, returned/
missing app coverage, server-only correlation assertions, trace plaintext/hash
absence, `ObservedEvent` business fields, and provider-neutral core
imports.

SQL/HTTPS/Command changes must additionally cover PostgreSQL and MySQL dialect
AST differentials, safe CTE and non-executing EXPLAIN, write/locking/function/
system-catalog rejection, read-only transaction and grant attestation, system-CA TLS 1.2 and
hostname verification, readable base-table discovery, deterministic time/stable
key inference, candidate-table overflow, exclusion reasons, no-safe-table
readiness, read-only transaction and cost budgets, canonical URL/SSRF/DNS/redirect/decompression controls, exact
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
heartbeats, analysis-to-reporting transition, report-before-job completion,
and expired-lease recovery in both investigation and reporting phases.

Execution-graph changes must additionally cover Workspace isolation,
deterministic node/edge ordering, parallel operations, repeated calls in one
Connector lane, unused frozen Connectors, phase priority and terminal mapping,
masked query states, per-node evidence references, report evidence projection,
artifact ownership, and the 100-record/256-KiB page limits. Workbench verification
must preserve node selection across canonical refresh, exercise compact/full
projection, evidence and current-step location, drawer pagination, all typed
Connector result presenters, absence of visible raw JSON, honor reduced motion, and
check English/Chinese light/dark layouts at 1440px, 768px, and 390px without
page-level overflow or overlap.

Source/model/report changes must additionally cover exact-SHA resolution with no
alert-source fallback, authoritative non-alert bound-branch freezing,
runtime-SHA and exact-anchor incompatibility, evidence-grounded source queries,
same-repository multi-query execution, exact-query reuse, repository relevance,
unparticipating-repository exclusion, credential/revision drift, source/config
authority matrices, strict source artifact provenance,
latency/reasoning routing, role and provider/deployment isolation, per-binding
budgets, tokenizer boundaries, pinned-context overflow, compaction reference and
literal drift, replay without another provider call, verifier disagreement,
immutable report retry, prompt/evidence injection, deterministic gold cases,
external-cause independence from code authority, full server assertion graphs,
the `215272664893440` Payssion sandbox regression, false-confirmed metrics, and
Wilson confidence release gates.

Before declaring work complete, assess architecture, dependency, and development-workflow impact. Update this file immediately when any of those contracts change, then verify the documented commands and behavior match the implementation.
