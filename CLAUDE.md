# Lode Development Context

## Dynamic Investigation Architecture

Lode investigates production incidents through a capability-constrained,
evidence-first graph rooted at investigations. The retired fixed seven-stage
pipeline is not a runtime or browser contract.

- At investigation start, the worker builds a persisted capability catalog from
  application-bound read-only Git repositories, active evidence connectors,
  approved data sources and diagnostic profiles, and inherited evidence.
- The worker creates the minimum wave-parallel plan graph from that catalog. A
  repository, Redis connector, data source, or observability backend that is
  not bound and active does not produce a node, a fake operation, or an
  unconfigured user-facing stage.
- When a missing capability is necessary to distinguish hypotheses, the graph
  creates an evidence_request node. Its persisted outcome states the missing
  fact, why it matters, the minimum redacted input, and an authorized
  alternative path. It never simulates a collector.
- Every node has an objective, selection reason, expected evidence, upstream
  dependencies, restricted tool input, budget, stop condition, append-only
  operation events, evidence references, outcome and next decision.
  investigation_plan_revisions records the initial plan and every replan
  decision, wave, and structural change set; investigation_plan_node_dependencies
  makes ordering auditable. A completed wave may add, cancel, reorder, or
  converge nodes instead of merely recording a cosmetic revision.
- Collector categories remain internal compatibility records because existing
  bounded collectors require investigation_stages foreign keys. The dashboard
  must render investigation_plan_nodes, never a fixed stage list.
- Node execution status is queued, running, succeeded, partial, blocked,
  failed, or canceled. Investigation execution status is only queued, running,
  completed, or failed. Conclusion maturity is independent: confirmed,
  provisional, insufficient, or unavailable. review_required is reserved for
  production-change approval or an audit violation, not for normal evidence
  gaps.

## Evidence And Autonomous Execution

- The worker may automatically invoke only registered, policy-approved,
  read-only collectors. AI never authors commands, SQL, URLs, credential
  references, or connector configuration. PostgreSQL, Redis, Kafka, and
  ClickHouse collection remains typed and administrator-template-bound.
- Source nodes derive at most eight terms, with exact error codes and symbols
  ahead of alert prose; they inspect only source-language files, rank a bounded
  candidate set, and let the configured model select at most the configured
  code-evidence cap. The model receives only redacted candidate snippets and
  can return only existing candidate indexes with a safe selection reason.
  Project memory files (AGENTS.md, AGENT.md, CLAUDE.md, README.md, README.*,
  and .github/AGENTS.md) are bounded orientation material, never root-cause
  proof. New source-file artifacts persist the immutable path, revision,
  snippet start/end, and matched line so the browser can open an exact
  read-only source anchor. If the alert has no deployed revision, the default
  branch is marked default_branch_reference, never presented as incident proof,
  and collected once as reference context rather than duplicated as incident
  and latest revision.
- Every operation writes a started event, may append bounded progress facts,
  and writes an explicit terminal event with the same operation_id. Input/output
  are redacted before persistence. Evidence artifacts are
  content-hashed and immutable; investigation_evidence_links records
  collected, inherited, and manual membership without duplicating evidence.
- Concurrent evidence waves use separate async sessions. After the coordinating
  session expires its identity map, capture node IDs and explicitly await a
  reload before reading those nodes; never dereference an expired ORM attribute
  and trigger implicit I/O in async code.
- When no model is configured or a structured response fails validation, the
  worker produces a deterministic, authoritative failure-boundary conclusion
  from alert facts. It records evidence that can refine the boundary without
  asking a user to verify the conclusion. A follow-up automatically creates an
  inherited investigation and supersedes the prior conclusion version.

## AI Audit And Reasoning

- AI receives only redacted immutable evidence. It can participate in planning
  review, evidence review, attribution, and node re-planning, but the browser
  receives an auditable summary of evidence to facts to hypothesis or
  counter-evidence to next decision, never raw internal reasoning.
- investigation_ai_invocations stores purpose, provider, model, outcome,
  latency, input/output/total tokens, token source (provider, estimated, or
  unavailable), safe failure code, evidence references and a display-safe
  summary. Provider-reported usage is exact; otherwise the local estimate is
  labelled as such.
- Invalid JSON, invalid evidence references, language validation failures, and
  provider failures retain their safe failure code and any available usage.
  ai_participated is true only when a model returned a usable response.
- API and final conclusion totals aggregate participating nodes, actual model
  calls, latency, input/output/total tokens, and exact-versus-estimated usage.
- investigation_findings and investigation_finding_edges form the citable
  browser reasoning graph. Relationships are supports, contradicts, caused_by,
  or needs_test; internal model reasoning is never persisted or shown.
- The attribution dossier is capped at 24 artifacts and ordered by causal
  value: incident-time runtime facts, fixed incident-revision code, relevant
  diff, reference code, then project context. The attribution prompt must stop
  at the observed boundary unless source behavior is tied to an alert contract,
  incident-time runtime fact, or verified incident revision; it must request
  the smallest discriminating evidence rather than guess a root cause.
- Every new reasoning packet also persists a reader-facing brief with a
  headline, summary, direct_cause, cited confirmed facts, impact, uncertainty,
  and next step. `direct_cause` is a single AI-attributed mechanism with a
  confirmed/not_proven state. A confirmed direct cause has one to three valid
  citations and may never cite repository_context artifacts such as README or
  project instructions. The brief is a presentation contract, not model
  chain-of-thought: confirmed claims require valid evidence references and
  explicit evidence gaps may carry none. Invalid or missing briefs fail safe
  into the deterministic new-run brief; a persisted incomplete brief is
  rejected with a reinvestigation-required response, never adapted in the
  browser.

## Intake, Follow-ups, And APIs

- Run API, consumer and worker independently with make serve, make consume,
  and make work. Kafka intake validates an alert, persists only the ingress
  stage and its operation facts, archives the alert evidence, records scope
  field provenance, and queues the job. It does not pre-create empty stages.
- Scope values have explicit sources. Do not infer a service from an
  application name; absent service/environment/version/trace values remain
  unknown and are surfaced with their missing source.
- GET /investigations/{id} returns capability catalog, dynamic nodes,
  dependencies, plan history, source-version basis, immutable evidence,
  coverage/open requirements, structured reasoning path, node and aggregate AI
  usage, scope provenance, conclusion version/supersession, inheritance links,
  reasoning edges, event cursor, recent live timeline, and execution.current_activity.
  Every visible event and node operation includes operation_id plus a
  server-generated display object (actor, headline, message, tone, and cited
  evidence references). The main workbench uses this presentation layer; raw
  event detail is reserved for the audit drawer.
  It also returns the new-workbench `brief`; source evidence exposes code only
  when its complete immutable anchor is present. Historical artifacts without
  that anchor are not synthesized into a code-viewer payload.
- GET /investigations/{id}/stream is an authenticated SSE stream. It supports
  Last-Event-ID / after replay, an initial snapshot, heartbeats, and terminal
  closure. Browser clients use bearer-authenticated streaming fetch because the
  API may be cross-origin.
- POST /investigations/{id}/follow-ups accepts bounded redacted operator
  evidence and an allowlisted scope patch. It creates a new queued
  investigation linked to its parent, copies immutable evidence membership as
  inherited, archives supplied content as operator_input, and reruns the
  graph. POST /investigations/{id}/reanalyze creates the same inheritance
  chain with no new manual payload.
- The workbench investigation page is one centered, responsive real-time
  workspace with no local views or tabs. It presents the reader-facing brief
  and reversible recommendation before the execution trust trail. The CI-style
  workflow is a horizontal flow canvas: compact wave/job nodes are connected
  by dependency and wave-transition arrows, while planning revisions appear as
  explicit update markers. It collapses low-level Git and collector operations
  into a few semantic milestones. The live console folds started/progress/terminal facts with the
  same operation_id into one lifecycle row; raw event JSON remains audit-only.
  The evidence explorer shows only the one-to-three references that prove an
  AI-confirmed direct cause. Candidate files and project context remain out of
  the reader path; while a direct cause is not proven, the workspace shows the
  exact evidence gap rather than a list of related files. A missing causal path
  is a compact evidence-threshold state, not a blank graph. `@xyflow/react` is
  reserved for the citable reasoning graph.
  AI usage, budgets, restricted boundaries, and raw execution detail live in an
  on-demand audit drawer. No historical payload adapter or legacy workbench is
  maintained for this UI.

## Database Cutover And Worker Verification

- 0001_initial, 0002_canonical_investigations, and
  0003_execution_events are immutable. 0004_dynamic_graph is the
  non-reversible dynamic-graph migration. It converts historic needs_review
  rows to completed plus conclusion maturity, adds graph/AI audit tables,
  creates immutable evidence memberships, and marks completed runs without
  operation events unverifiable. 0005_realtime_investigation_v2 adds conclusion
  versioning/supersession, plan waves/change sets, and immutable reasoning
  edges. 0006_investigation_live_progress extends the immutable execution-event
  phase constraint with bounded progress facts for the live console. Both are
  additive and non-reversible; neither backfills or adapts historical runs.
- Apply migrations intentionally with uv run alembic upgrade head against the
  target environment. Do not run it against an unreviewed remote
  LODE_DATABASE_URL. Static review uses uv run alembic upgrade head --sql.
- Before activating a deployment, verify the API, consumer, and worker are all
  running code with ENGINE_VERSION = dynamic-graph-v2 and the database
  alembic_version is 0006_investigation_live_progress. Drain/restart workers after the
  migration so no process creates legacy fixed-stage rows. A worker failure
  must leave the job and investigation terminal state explicit; it must not
  fall back to the retired runner.

## Verification Expectations

- Cover capability selection: absent Redis/data source means no corresponding
  node; an active bound capability permits only its registered collector; a
  necessary unavailable capability becomes an evidence request.
- Cover plan dependencies, real replan change sets, wave parallelism,
  cancellation, budgets, unsafe parameter rejection, operation-event
  start/progress/terminal pairing, source incident version absence without
  duplicate default-branch collection, exact-term source ranking, model source
  candidate selection, and evidence inheritance.
- Cover provider-exact and locally-estimated token accounting, unavailable and
  failed model calls, node/overall aggregation, language/citation validation,
  direct-cause citation validation (including project-context rejection),
  deterministic failure-boundary conclusions, conclusion supersession, SSE
  replay/reconnect, reasoning-edge citation validation, and the boundary for
  production-change approval.
- Validate the web UI at desktop, tablet and mobile widths, including centered
  wide-workspace layout, workflow/log selection, live-console follow/pause,
  evidence-to-code anchor jumps, and the audit drawer. Run pnpm
  --dir apps/web typecheck and pnpm --dir apps/web build before handoff,
  alongside focused Python investigation tests for brief citation validation
  and strict source-anchor contracts.
