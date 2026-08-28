# Evidence Access Plane Threat Model

Status: Phase 0 frozen baseline; Phase 4 kernel and Phase 5/6 native connectors implemented

Owners:

| Boundary | Security owner | Runtime owner |
|---|---|---|
| Candidate schema and action relevance | Application | Investigation Engine |
| Native grammar and read-only proof | Security | Evidence Access Policy |
| Snapshot scope and budget intersection | Security | Evidence Access Policy |
| Credential injection and provider request | Platform | Connector Adapter |
| Durable authorization and replay prevention | Platform | Evidence Read Orchestrator |
| Command isolation | Security | Isolated Command Runner |
| Artifact masking and sealed values | Security | Evidence Store |

## Assets And Trust Boundaries

Protected assets are connector credentials, sealed evidence values, Workspace
authorization scopes, production availability, private evidence, immutable audit
history, and report integrity. AI output, repository content, alert content,
external responses, schema descriptions, and operator guidance are untrusted.

The AI process has no credential, network socket, subprocess, database session,
or writable filesystem. A `NativeReadCandidate` is data, never an executable
capability. Only an unexpired, single-use `AuthorizedEvidenceRead` whose hashes
cover the candidate, snapshot, policy, parser, adapter, and effective budget may
reach an execution adapter.

## Mandatory Pipeline

Every language passes, in order: strict schema validation, snapshot ownership,
complete parse, single-action proof, read-only proof, evidence relevance, scope
intersection, mandatory constraint injection, sealed ValueRef binding and
shape-preserving reparse, durable authorization, provider preflight, isolated
execution, postcondition validation, masking, and immutable archive.

Failure is closed. The service may add a mandatory predicate or reduce a
window, limit, projection, timeout, or output budget. Any other semantic rewrite
is rejected with a stable code and returned to the planner for at most one
regeneration.

## Language Threats And Required Controls

| Language | Primary threats | Required proof | Infrastructure backstop |
|---|---|---|---|
| LogQL | selector escape, DNF expansion, parser differential, unbounded range, attacker regexp/cardinality, rule or delete endpoints | Full maintained AST, normalized bounded condition tree, positive exact matcher per branch, server-only regexp escaping, bounded absolute time, allowed pipeline/aggregation nodes | Query-only token, endpoint allowlist, provider limits |
| Elasticsearch DSL | index escape, script/runtime fields, async/scroll persistence, aggregation explosion, management API | Exact non-reserved index allowlist at creation, JSON duplicate-key rejection, recursive node allowlist, exact `_search` path, forced range/size/source/bucket limits | Read-only role restricted to frozen indices |
| OpenSearch DSL | Elasticsearch policy reuse despite version/plugin differences, scripts, PPL/SQL, management API | Exact non-reserved index allowlist at creation, independent versioned parser/policy and contract corpus, exact `_search` path | Read-only role, plugins disabled |
| SQL | operator-selected unsafe tables, multi-statement, writable CTE, locking reads, file/network/UDF side effects, system catalogs, TLS interception, cost exhaustion | Fixed dialect AST, every node read-only, introspected safe-table catalog, enforced limit/timeouts, optional non-executing explain | Explicit TLS 1.2+ `verify_full` or encryption-only `require`, no plaintext/fallback, non-privileged identity, read-only transaction, scoped no-write grant proof, resource group |
| HTTP(S) | Ambiguous normalization, redirects, credential override, nominal GET with side effects, plaintext transport | Canonical HTTP(S) URL, safe-read endpoint catalog, exact scheme/host/port/path/schema and response checks | Zero redirects, adapter-injected identity, operator-controlled network for HTTP |
| Command | shell injection, interpreter escape, path/symlink escape, writable mount, inherited environment/network, binary replacement | Structured executable/argv/working-set, exact flag grammar, fixed binary path and hash | Separate uid/image, read-only mounts/root, empty environment, no network, syscall/resource limits |

The SQL policy uses the fixed SQLGlot parser for PostgreSQL and MySQL and
enables only bounded SELECT, one non-recursive read-only CTE, and non-executing
EXPLAIN evidence. The generic HTTPS adapter has no request body or redirect
capability. The initial command catalog contains only one exact
`/usr/bin/rg --fixed-strings` profile; adding another profile requires a code
and threat-model change, not a configuration edit.

Loki scope is authored as an `ALL`/`ANY` condition tree and normalized by the
server into deterministic DNF before it is frozen. Depth is at most three,
normalized branches at most eight, conditions at most 32, and set values at
most 20. Every branch contains a positive exact matcher: `equals` or `any_of`.
The `any_of` and `not_any_of` regular expressions are produced only by escaping
literal values in server code. The model parser continues to reject regex. Each branch receives a share
of the total timeout and the same global row/byte/window budget. Results merge
by timestamp, labels, and value with stable ordering. One failed or partial
branch fails the read, so no partial result is archived.
Third-party HTTP evidence Connectors may use canonical HTTP or HTTPS origins,
including authentication on operator-controlled private networks. The chosen
scheme and exact port are mandatory in generic endpoint scopes. Redirect,
embedded-credential, timeout, and response-bound checks remain unchanged.
Operators own the confidentiality
risk when credentials or evidence traverse plaintext HTTP.
Generic HTTP(S) Connector kind version 2 requires this explicit scheme; older
scope payloads are not inferred or upgraded.

PostgreSQL and MySQL connector forms expose no allowed-table, time-column,
stable-order, `search_path`, plaintext, or TLS-fallback authority. `verify_full`
is the default and verifies hostname against system roots plus an optional
bounded Connector CA PEM; parsing rejects malformed certificates and private
keys. Explicit `require` keeps TLS mandatory while accepting the documented
server-identity risk, and cannot carry a CA value.
New PostgreSQL
Connectors require one to 32 exact non-system Schema names. The discovery query
parameterizes the frozen allowlist, requires every requested Schema to exist and
be accessible to the current role, and enumerates only SELECT-readable tables in
that range. PostgreSQL scopes without the field are invalid rather than inferred;
MySQL remains bounded to its configured database.
PostgreSQL may connect to a primary or replica. A replica remains the preferred
deployment boundary, but creation instead requires the server to honor an
explicit read-only transaction and rejects superuser, role/database creation,
replication, `BYPASSRLS`, database ownership, and `pg_write_all_data` authority.
Scope discovery also rejects table- or column-level writes, sequence updates,
and Schema creation within every allowed Schema. This proof composes with the
fixed SELECT/function allowlist and explicit read-only execution transactions.
PostgreSQL discovery performs a fixed four-query catalog sequence for all
selected tables and is bounded by one 10-second wall-clock deadline, avoiding
an attacker-amplifiable per-table network round trip.
After topology and identity attestation, discovery fails when more than 200
candidates are visible. A safe table requires a non-null
temporal column and either a primary key or an all-non-null unique index;
selection order is deterministic and every excluded table receives a reason
code. An empty discovery is not snapshot-ready. Connections use the system plus
optional Connector-scoped trust store, hostname verification, and TLS 1.2 or
newer.

Connector creation is fail-before-write: strict kind-specific validation, remote
verification, and scope discovery finish before one transaction persists the
healthy Connector, encrypted credentials, final scope, catalog, and success
audit. Verification, inaccessible scope, empty safe discovery, and provider
failure leave no Connector or audit residue.
Connector verification and discovery errors expose only code-authored provider
reasons plus an allowlist of non-secret details such as observed/supported
versions, failed PostgreSQL read-only checks, safe SQLSTATE values, and HTTP
status. PostgreSQL authentication, database selection, TLS, connection-capacity,
permission, timeout, writable-session, scoped-write-grant, and privileged-account
failures remain distinct actionable messages. Raw exceptions, response bodies,
URLs with embedded credentials, and secret material are never returned to the
client.
The database secret-free trigger recursively scans ordinary Connector and scope
configuration, but not server-generated Schema catalogues. Catalogues contain
provider identifiers and type/policy metadata without row values, so legitimate
column names such as `token` or `password` are not treated as credentials.

The command runner is separately authenticated and replay protected. It
revalidates binary hash, argv, budget, logical working root and every path
component after the worker policy. Each bubblewrap invocation has no network,
an empty environment and only its exact authorized file mounted read-only.
The outer container has a read-only root, no writable volumes, a private network,
separate uid, dropped capabilities, no-new-privileges and resource limits. API
and consumer receive neither its key nor its network. A runner-specific seccomp
profile permits namespace construction but rejects BPF, ptrace, keyring,
cross-process memory, kernel-module, host-control and performance-monitoring
syscalls. The runner has no product-level enable switch. A new execution is
allowed only when its frozen Connector remains active at authorization time;
disabled Connectors reject future reads while preserving immutable audit.
Private-key and cloud
access-key output is discarded; only SHA-256, category and rejection metadata
cross the runner boundary. Unprivileged user namespaces are a deployment
prerequisite. A host that disables them produces `sandbox_violation`; the
runner never falls back to unsandboxed execution.

## Cross-Cutting Abuse Cases

- A model claim that a candidate is safe never changes policy.
- A discovered resource never expands a repository binding or evidence scope.
- Sentinels are valid only in approved AST value nodes. Values are bound after
  authorization checks, reparsed, and cannot alter AST shape.
- Credential fields, authorization headers, cookies, absolute host paths,
  environment assignments, shell strings, provider management endpoints, and
  unbounded queries are never accepted from a candidate.
- Decompressed responses are bounded while streaming and scanned for secrets
  and prompt injection before storage or model use.
- All registered native languages and execution adapters are available to the
  planner. The planner decides whether they are relevant; Connector lifecycle
  and health checks block new authorization without deleting audit.

## Stable Rejection Classes

`invalid_syntax`, `unsupported_node`, `write_semantics`, `scope_violation`,
`budget_violation`, `sandbox_violation`, and
`preflight_failed` are the complete V1 policy rejection classes. Provider,
transport, timeout, and result failures are recorded separately as execution
failures and never relabeled as policy decisions.

## Review Checklist

- [ ] Candidate JSON rejects unknown fields, duplicate keys, excessive depth,
      invalid Unicode, and oversized values.
- [ ] A maintained parser consumes the complete payload without warnings or
      ignored suffixes.
- [ ] Every AST/JSON/argv node is positively allowlisted.
- [ ] Snapshot ownership and root scope are checked before ValueRef unsealing.
- [ ] Loki condition trees satisfy depth/condition/value/branch limits, every
      DNF branch has a positive exact matcher, and only server code creates regex.
- [ ] Value binding preserves the parsed shape.
- [ ] Absolute window, result, byte, timeout, concurrency, and total budgets are
      enforced below AI requests.
- [ ] Credentials and provider identities are injected by the adapter only.
- [ ] The infrastructure identity is independently read-only.
- [ ] SQL scope comes only from bounded schema introspection over system-CA,
      hostname-verified TLS 1.2+; excluded and empty scopes remain fail-closed.
- [ ] Authorization tokens are signed/hash-bound, expiring, and single-use.
- [ ] Candidate, decisions, effective action, attempts, results, and rejection
      reasons are immutable and replayable.
- [ ] Positive, negative, parser-differential, scope, budget, and bypass tests
      cover the exact parser/policy/adapter versions.
- [ ] Connector disable/reenable and in-flight authorization behavior have been
      exercised.

No native language or connector may become active until every applicable item
is supported by code, contract tests, and deployment policy.
