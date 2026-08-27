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
| LogQL | selector escape, parser differential, unbounded range, costly regexp/cardinality, rule or delete endpoints | Full maintained AST, root selector subset, bounded absolute time, allowed pipeline/aggregation nodes | Query-only token, endpoint allowlist, provider limits |
| Elasticsearch DSL | index escape, script/runtime fields, async/scroll persistence, aggregation explosion, management API | JSON duplicate-key rejection, recursive node allowlist, exact `_search` path, forced range/size/source/bucket limits | Read-only role restricted to frozen indices |
| OpenSearch DSL | Elasticsearch policy reuse despite version/plugin differences, scripts, PPL/SQL, management API | Independent versioned parser/policy and contract corpus, exact `_search` path | Read-only role, plugins disabled |
| SQL | multi-statement, writable CTE, locking reads, file/network/UDF side effects, system catalogs, cost exhaustion | Fixed dialect AST, every node read-only, catalog allowlist, enforced limit/timeouts, optional non-executing explain | Attested replica/snapshot, read-only role and transaction, resource group |
| HTTPS | Ambiguous normalization, redirects, credential override, nominal GET with side effects | Canonical HTTPS URL, safe-read endpoint catalog, host/port/path/schema and response checks | Zero redirects, adapter-injected identity |
| Command | shell injection, interpreter escape, path/symlink escape, writable mount, inherited environment/network, binary replacement | Structured executable/argv/working-set, exact flag grammar, fixed binary path and hash | Separate uid/image, read-only mounts/root, empty environment, no network, syscall/resource limits |

The SQL policy uses the fixed SQLGlot parser for PostgreSQL and MySQL and
enables only bounded SELECT, one non-recursive read-only CTE, and non-executing
EXPLAIN evidence. The generic HTTPS adapter has no request body or redirect
capability. The initial command catalog contains only one exact
`/usr/bin/rg --fixed-strings` profile; adding another profile requires a code
and threat-model change, not a configuration edit.

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
- [ ] Value binding preserves the parsed shape.
- [ ] Absolute window, result, byte, timeout, concurrency, and total budgets are
      enforced below AI requests.
- [ ] Credentials and provider identities are injected by the adapter only.
- [ ] The infrastructure identity is independently read-only.
- [ ] Authorization tokens are signed/hash-bound, expiring, and single-use.
- [ ] Candidate, decisions, effective action, attempts, results, and rejection
      reasons are immutable and replayable.
- [ ] Positive, negative, parser-differential, scope, budget, and bypass tests
      cover the exact parser/policy/adapter versions.
- [ ] Connector disable/reenable and in-flight authorization behavior have been
      exercised.

No native language or connector may become active until every applicable item
is supported by code, contract tests, and deployment policy.
