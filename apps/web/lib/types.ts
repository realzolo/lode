export interface CurrentUser {
  id: number;
  username: string;
  display_name: string;
  status: 'active' | 'disabled';
  is_system_admin: boolean;
  must_change_password: boolean;
  created_at: string;
}

export interface Workspace {
  id: number;
  name: string;
  description: string;
  ingestion_topic: string;
  model_policy_revision_id: number | null;
  architecture_context_revision_id: number | null;
  ingestion_state: 'draft' | 'active' | 'paused';
  ingestion_version: number;
  ingestion_start_position: 'earliest' | 'latest' | null;
  ingestion_started_at: string | null;
  ingestion_paused_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceReadiness {
  workspace_id: number;
  can_start: boolean;
  checks: Array<{
    code: 'kafka_topic' | 'model_policy' | 'repositories' | 'evidence_connectors' | 'architecture_context';
    outcome: 'passed' | 'blocked' | 'warning';
    details: Record<string, unknown>;
  }>;
  runtime: {
    observed_state: 'idle' | 'starting' | 'listening' | 'paused' | 'error';
    observed_version: number;
    consumer_id: string | null;
    assigned_partitions: number;
    backlog: number | null;
    last_heartbeat_at: string | null;
    last_error: string | null;
  };
}

export type ArchitectureContextKind = 'system_purpose' | 'architecture' | 'critical_flow' | 'dependency' | 'operational_convention';

export interface WorkspaceArchitectureContext {
  id: number;
  workspace_id: number;
  entries: Array<{ kind: ArchitectureContextKind; title: string; content: string }>;
  revision: number;
  created_at: string;
}

export interface WorkspaceMember {
  user_id: number;
  username: string;
  display_name: string;
  status: 'active' | 'disabled';
  permission: 'viewer' | 'operator';
}

export interface PlatformSettings {
  ai_output_language: 'en' | 'zh';
  supported_languages: Array<'en' | 'zh'>;
  revision: number;
  updated_at: string;
}

export interface ProviderAccount {
  id: number;
  name: string;
  provider_kind: 'openai' | 'anthropic';
  protocol_id: 'openai.responses.v1' | 'openai.chat_completions.v1' | 'anthropic.messages.v1';
  base_url: string;
  state: 'active' | 'disabled';
  verification_status: 'untested' | 'healthy' | 'unavailable';
  verified_at: string | null;
  models: ProviderAccountModel[];
  revision: number;
}

export interface ProviderAccountModel {
  id: number;
  provider_account_id: number;
  provider_model_id: string;
  display_name: string;
  capabilities: Record<string, boolean>;
  discovery_state: 'discovered' | 'manual' | 'missing';
  availability_state: 'untested' | 'healthy' | 'unavailable';
  state: 'active' | 'disabled';
  revision: number;
}

export interface ProviderModelCatalogItem {
  provider_kind: 'openai' | 'anthropic';
  provider_model_id: string;
  display_name: string;
  context_window_tokens: number;
  max_output_tokens: number;
  capabilities: Record<string, boolean>;
  protocol_ids: string[];
  catalog_revision: string;
  source_url: string;
  reviewed_at: string;
}

export interface ProviderModelDiscovery {
  catalog_revision: string;
  available_model_ids: string[];
  unsupported_model_ids: string[];
}

export type ModelDataClass = 'masked' | 'source_code' | 'internal' | 'restricted';

export interface ModelBindingCreateInput {
  provider_account_model_id: number;
  execution_classes: string[];
  allowed_roles: string[];
  priority: number;
  max_calls: number;
  max_cost_per_call: number;
  timeout_ms: number;
  allowed_data_classes: ModelDataClass[];
  max_context_utilization: number;
}

export interface ModelBinding {
  id: number;
  workspace_id: number;
  provider_account_model_id: number;
  execution_classes: string[];
  allowed_roles: string[];
  priority: number;
  max_calls: number;
  max_cost_per_call: number;
  timeout_ms: number;
  allowed_data_classes: ModelDataClass[];
  max_context_utilization: number;
  state: 'active' | 'disabled';
  revision: number;
}

export interface RepositoryBinding {
  id: number;
  workspace_id: number;
  repository_id: number;
  account_connection_id: number;
  account_name: string;
  external_account_login: string;
  provider_kind: 'github' | 'gitlab' | 'gitee';
  name: string;
  full_name: string;
  repo_url: string;
  web_url: string;
  repo_type: string;
  default_branch: string;
  branch_mode: 'default' | 'branch';
  branch_name: string | null;
  effective_branch: string;
  analysis_mode: 'code' | 'documentation';
  is_alert_source: boolean;
  priority: number;
  description: string;
  state: 'active' | 'disabled';
  revision: number;
}

export interface RepositoryAnalysisJob {
  id: number;
  workspace_id: number;
  requested_binding_ids: number[];
  state: 'queued' | 'running' | 'succeeded' | 'failed';
  result_status: 'pending' | 'clean' | 'warnings' | 'failed';
  is_current: boolean;
  attempt: number;
  source_branches: Record<string, string>;
  source_revisions: Record<string, string>;
  graph_revision_id: number | null;
  scanned_file_count: number;
  issue_count: number;
  failure_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface GitBranchPage {
  items: Array<{ name: string; is_default: boolean }>;
  next_cursor: string | null;
}

export interface RepositoryAnalysisIssue {
  id: number;
  repository_analysis_job_id: number;
  repository_binding_id: number | null;
  ordinal: number;
  severity: 'warning' | 'error';
  code: string;
  path: string | null;
  detail: string;
  created_at: string;
}

export interface RepositoryAnalysisIssuePage {
  items: RepositoryAnalysisIssue[];
  next_cursor: number | null;
}

export interface GitAccount {
  id: number;
  adapter_id: 'github' | 'gitlab' | 'gitee';
  api_url: string;
  name: string;
  external_account_id: string;
  external_account_login: string;
  account_url: string;
  state: 'active' | 'disabled' | 'revoked';
  verification_status: 'untested' | 'healthy' | 'unavailable';
  verified_at: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  repository_count: number;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface GitAdapter {
  id: 'github' | 'gitlab' | 'gitee';
  display_name: string;
  official_api_url: string;
  custom_endpoint_allowed: boolean;
}

export interface GitAccountRepository {
  repository_id: number;
  provider_kind: 'github' | 'gitlab' | 'gitee';
  full_name: string;
  repo_url: string;
  web_url: string;
  default_branch: string;
  visibility: 'public' | 'private' | 'internal';
  archived: boolean;
}

export interface BuildUnit {
  id: number;
  repository_binding_id: number;
  stable_key: string;
  source_root: string;
  build_system: string;
  manifest_paths: string[];
  entrypoints: string[];
  identity_status: 'verified' | 'provisional' | 'ambiguous';
  state: 'active' | 'disabled';
  revision: number;
}

export interface Component {
  id: number;
  stable_key: string;
  display_name: string;
  kind: string;
  description: string;
  identity_status: 'verified' | 'provisional' | 'ambiguous';
  state: 'active' | 'disabled';
  revision: number;
  source_bindings: Array<{
    build_unit_id: number;
    build_unit_key: string;
    role: string;
    path_prefix: string;
  }>;
}

export interface EvidenceConnector {
  id: number;
  workspace_id: number;
  name: string;
  kind: string;
  kind_version: number;
  config: Record<string, unknown>;
  instance_revision: number;
  state: 'active' | 'disabled';
  verification_status: 'untested' | 'healthy' | 'unavailable';
  verified_at: string | null;
  last_error: string | null;
  capabilities: string[];
  last_introspected_at: string | null;
  configured_secret_fields: string[];
}

export type LokiFilterInput = {
  kind: 'group';
  combinator: 'all' | 'any';
  items: Array<LokiFilterInput | {
    kind: 'condition';
    label: string;
    operator: 'equals' | 'not_equals' | 'any_of' | 'not_any_of';
    values: string[];
  }>;
};

type ConnectorCreateBase = { name: string };
type AuthenticatedConnectorCreate = {
  authentication: 'bearer_token' | 'api_key' | 'basic';
  credential: string;
  credential_username?: string;
};
type DatabaseConnectorCreate = ConnectorCreateBase & {
  host: string;
  port?: number;
  database: string;
  database_username: string;
  database_password: string;
  tls_mode: 'verify_full' | 'require';
  ca_certificate_pem?: string;
};
type ClickHouseConnectorCreate = Omit<DatabaseConnectorCreate, 'tls_mode'> & {
  kind: 'clickhouse';
  tls_mode: 'verify_full' | 'require' | 'disabled';
};

export type ConnectorCreateInput =
  | (ConnectorCreateBase & {
    kind: 'loki';
    endpoint: string;
    authentication: 'none' | 'bearer_token';
    credential?: string;
    tenant_id?: string;
    root_filter: LokiFilterInput;
  })
  | (ConnectorCreateBase & AuthenticatedConnectorCreate & {
    kind: 'elasticsearch' | 'opensearch';
    endpoint: string;
    allowed_indices: string[];
  })
  | (DatabaseConnectorCreate & {
    kind: 'postgresql';
    allowed_schemas: string[];
  })
  | (DatabaseConnectorCreate & { kind: 'mysql' })
  | ClickHouseConnectorCreate
  | (ConnectorCreateBase & AuthenticatedConnectorCreate & {
    kind: 'prometheus' | 'tempo' | 'jaeger' | 'argocd';
    endpoint: string;
  })
  | (ConnectorCreateBase & AuthenticatedConnectorCreate & {
    kind: 'kubernetes';
    endpoint: string;
    namespace: string;
  })
  | (ConnectorCreateBase & AuthenticatedConnectorCreate & {
    kind: 'github';
    endpoint: string;
    owner: string;
    repository: string;
  })
  | (ConnectorCreateBase & AuthenticatedConnectorCreate & {
    kind: 'gitlab';
    endpoint: string;
    project_id: number;
  })
  | (ConnectorCreateBase & AuthenticatedConnectorCreate & {
    kind: 'https';
    endpoint: string;
    verification_path?: string;
    safe_read_path: string;
  });

export type EntityId = number;

export type IncidentState = 'open' | 'acknowledged' | 'mitigated' | 'resolved' | 'closed';
export type IncidentSeverity = 'UNCLASSIFIED' | 'WARNING' | 'CRITICAL';

export interface IncidentActionCapability {
  action: 'acknowledge' | 'mitigate' | 'resolve' | 'close' | 'reopen' | 'start_investigation' | 'assign' | 'create_action' | 'review';
  allowed: boolean;
  reason_code: string | null;
}

export interface IncidentSignal {
  id: number;
  schema_version: 'incident-signal.v1';
  source_type: 'kafka' | 'manual';
  source_event_id: string | null;
  signal_kind: 'firing' | 'recovered';
  observed_at: string;
  severity: IncidentSeverity;
  title: string;
  summary: string;
  repository_binding_id: number | null;
  has_trace: boolean;
  source_revision: string | null;
  fingerprint: string;
  error_masked: Record<string, unknown>;
}

export interface InvestigationRun {
  id: number;
  status: 'queued' | 'running' | 'paused' | 'reporting' | 'completed' | 'failed' | 'cancelled';
  result_state: 'pending' | 'confirmed' | 'hypothesis' | 'insufficient' | 'unavailable';
  trigger_reason: 'initial' | 'severity_escalation' | 'evidence_change' | 'recovery' | 'operator_request' | 'retry' | 'resumed' | 'evidence_added' | 'follow_up' | 'hypothesis_branch';
  trigger_signal_id: number | null;
  parent_investigation_id: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface IncidentTimelineEvent {
  id: number;
  event_type: string;
  actor_id: number | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface IncidentAction {
  id: number;
  investigation_id: number | null;
  action_type: 'mitigate' | 'remediate' | 'validate' | 'prevent';
  status: 'open' | 'in_progress' | 'blocked' | 'validation' | 'completed' | 'cancelled';
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  title: string;
  rationale: string;
  validation: string;
  evidence_refs: number[];
  owner_id: number;
  created_by: number | null;
  state_version: number;
  created_at: string;
  updated_at: string;
}

export interface IncidentSummary {
  id: number;
  workspace_id: number;
  title: string;
  severity: IncidentSeverity;
  state: IncidentState;
  signal_count: number;
  first_occurred_at: string;
  last_occurred_at: string;
  assigned_to: number | null;
  state_version: number;
  recurrence_of_id: number | null;
}

export interface IncidentListPage {
  items: IncidentSummary[];
  next_cursor: string | null;
}

export interface IncidentOverview extends IncidentSummary {
  state_changed_at: string;
  allowed_actions: IncidentActionCapability[];
  signals: IncidentSignal[];
  investigations: InvestigationRun[];
  timeline: IncidentTimelineEvent[];
  actions: IncidentAction[];
}

export interface CausalNode {
  node_id: string;
  node_type: 'impact' | 'trigger' | 'root_cause' | 'contributing_factor' | 'propagation' | 'detection' | 'mitigation' | 'recovery' | 'counter_evidence' | 'evidence_gap';
  status: 'confirmed' | 'hypothesis' | 'refuted' | 'unknown';
  statement: string;
  evidence_refs: number[];
  entity_refs: number[];
}

export interface CausalEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  status: 'confirmed' | 'hypothesis' | 'refuted';
  relation: 'triggers' | 'causes' | 'contributes_to' | 'propagates_to' | 'detected_by' | 'mitigated_by' | 'recovers' | 'contradicts';
  statement: string;
  evidence_refs: number[];
}

export interface CausalGraph {
  nodes: CausalNode[];
  edges: CausalEdge[];
  root_node_ids: string[];
}

export interface InvestigationCodeFindingView {
  id: number;
  status: 'confirmed' | 'hypothesis' | 'no_defect' | 'not_found';
  source_artifact_id: number | null;
  repository_id: number | null;
  revision: string | null;
  revision_origin: string | null;
  path: string | null;
  symbol: string | null;
  start_line: number | null;
  end_line: number | null;
  issue_type: string | null;
  faulty_behavior: string;
  why_wrong: string;
  expected_behavior: string;
  trigger_condition: string;
  propagation: string[];
  incident_evidence_refs: number[];
  supporting_evidence_refs: number[];
  counter_evidence_refs: number[];
  missing_validation: string[];
  test_scenario: string;
}

export interface SourceAssessmentView {
  repository_id: number;
  build_unit_id: number | null;
  component_id: number | null;
  revision: string | null;
  revision_origin: 'alert_revision' | 'bound_branch_head' | 'runtime_observed';
  authority_status: 'authoritative' | 'corroborated' | 'contradicted' | 'unavailable';
  compatibility_status: 'not_checked' | 'compatible' | 'incompatible';
  mismatch_reasons: string[];
  evidence_refs: number[];
}

export interface ConfigurationAssessmentView {
  scope: string;
  declared_value: unknown;
  runtime_value: unknown;
  effective_status: 'unknown' | 'corroborated' | 'contradicted';
  evidence_refs: number[];
}

export interface InvestigationComparisonRunView {
  run: {
    id: number;
    parent_investigation_id: number | null;
    trigger_reason: string;
    status: InvestigationRun['status'];
    result_state: 'pending' | InvestigationReportView['result_state'];
    window_started_at: string;
    window_finished_at: string;
  };
  input: {
    signal_id: number | null;
    title: string;
    summary: string;
    source_type: string;
    repository_binding_id: number | null;
    source_revision: string | null;
  } | null;
  hypotheses: unknown[];
  evidence: Array<{ id: number; artifact_kind: string; content_hash: string; source_revision: string | null }>;
  causal_graph: CausalGraph | null;
  conclusion: {
    result_state: InvestigationReportView['result_state'];
    headline: string;
    executive_summary: string;
    causal_graph: CausalGraph;
  } | null;
}

export interface InvestigationComparisonView {
  incident_id: number;
  left: InvestigationComparisonRunView;
  right: InvestigationComparisonRunView;
}

export interface InvestigationReportView {
  schema_version: 'investigation-report.v1';
  result_state: 'confirmed' | 'hypothesis' | 'insufficient' | 'unavailable';
  headline: string;
  executive_summary: string;
  impact_scope: Array<{ text: string; evidence_refs: number[] }>;
  causal_graph: CausalGraph;
  participants: Array<{ entity_ref: number; display_name: string; identity_status: string; evidence_refs: number[] }>;
  timeline_summary: Array<{ occurred_at: string; event_ref: number; summary: string; evidence_refs: number[] }>;
  source_assessments: SourceAssessmentView[];
  configuration_assessments: ConfigurationAssessmentView[];
  counter_evidence: Array<{ text: string; evidence_refs: number[] }>;
  evidence_gaps: Array<{ description: string; consequence: string; required_evidence: string; related_node_ids: string[] }>;
  action_recommendations: Array<{ action_type: string; priority: string; title: string; rationale: string; validation: string; evidence_refs: number[] }>;
  code_findings: InvestigationCodeFindingView[];
}

export interface InvestigationOverview {
  id: EntityId;
  incident_id: EntityId;
  workspace_id: number;
  status: InvestigationRun['status'];
  result_state: 'pending' | 'confirmed' | 'hypothesis' | 'insufficient' | 'unavailable';
  trigger_reason: InvestigationRun['trigger_reason'];
  output_language: 'en' | 'zh';
  title: string | null;
  summary: string | null;
  severity: IncidentSeverity | null;
  observed_at: string | null;
  error_type: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  report: Pick<InvestigationReportView, 'headline' | 'executive_summary' | 'impact_scope' | 'causal_graph' | 'evidence_gaps' | 'action_recommendations'> | null;
  operation_count: number;
  evidence_count: number;
}

export interface CorrelationCandidate {
  id: number;
  signal_id: number;
  current_incident_id: number;
  candidate_incident_id: number;
  score: number;
  factors: Record<string, unknown>;
  status: 'pending' | 'accepted' | 'rejected';
  created_at: string;
}

export interface InvestigationReview {
  id: number;
  code_finding_id: number | null;
  verdict: 'accepted' | 'rejected' | 'needs_evidence';
  comment: string;
  reviewer_id: number;
  supersedes_review_id: number | null;
  created_at: string;
}

export interface ActionProposal {
  id: number;
  investigation_id: number;
  action_type: 'mitigate' | 'remediate' | 'validate' | 'prevent';
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  title: string;
  rationale: string;
  validation: string;
  evidence_refs: number[];
  decision: 'accepted' | 'rejected' | null;
  action_id: number | null;
  created_at: string;
}

export interface SimilarIncident {
  incident_id: number;
  investigation_id: number;
  headline: string;
  executive_summary: string;
  causal_signature: string[];
  similarity: number;
  clue_only: true;
}

export type InvestigationExecutionNodeType = 'input' | 'decision' | 'operation' | 'synthesis' | 'verification' | 'report' | 'phase';
export type InvestigationExecutionPhase = 'queued' | 'planning' | 'executing' | 'reporting' | 'completed' | 'failed';

export interface InvestigationExecutionLane {
  id: string;
  kind: 'control' | 'connector' | 'repository';
  label: string;
  subtitle: string | null;
  connector_kind: string | null;
  snapshot_id: number | null;
}

export interface InvestigationExecutionStage {
  index: number;
  kind: 'input' | 'decision' | 'execution' | 'reporting' | 'result';
  ordinal: number | null;
}

export interface InvestigationExecutionNode {
  id: string;
  node_type: InvestigationExecutionNodeType;
  lane_id: string;
  stage_index: number;
  round_ordinal: number | null;
  status: string;
  title: string;
  subtitle: string | null;
  purpose: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  evidence_count: number;
  evidence_refs: number[];
  record_count: number | null;
  failure_code: string | null;
  detail_available: boolean;
}

export interface InvestigationExecutionEdge {
  id: string;
  source: string;
  target: string;
  kind: 'sequence' | 'dispatch' | 'continue' | 'report';
  status: 'default' | 'complete' | 'active' | 'failed';
}

export interface InvestigationUnusedConnector {
  snapshot_id: number;
  connector_id: number;
  name: string;
  kind: string;
  allowed_languages: string[];
  reason_code: string | null;
}

export interface InvestigationExecutionGraph {
  schema_version: 'investigation-execution-graph.v1';
  investigation_id: number;
  status: string;
  phase: InvestigationExecutionPhase;
  event_cursor: number;
  active_node_ids: string[];
  lanes: InvestigationExecutionLane[];
  stages: InvestigationExecutionStage[];
  nodes: InvestigationExecutionNode[];
  edges: InvestigationExecutionEdge[];
  unused_connectors: InvestigationUnusedConnector[];
}

export interface InvestigationExecutionArtifactSummary {
  id: number;
  kind: string;
  evidence_class: string;
  data_class: string;
  record_count: number | null;
  archived_at: string;
}

export interface InvestigationExecutionArtifactPage {
  artifact_id: number;
  artifact_kind: string;
  metadata: Record<string, unknown>;
  items: unknown[];
  total_items: number;
  after_index: number;
  next_after_index: number | null;
  preview_bytes: number;
  item_truncated: boolean;
}

export interface InvestigationExecutionNodeDetail {
  schema_version: 'investigation-execution-node.v1';
  node_id: string;
  node_type: InvestigationExecutionNodeType;
  status: string;
  title: string;
  overview: Record<string, unknown>;
  query: Record<string, unknown> | null;
  authorization: Record<string, unknown> | null;
  execution: Record<string, unknown> | null;
  events: Array<Record<string, unknown>>;
  artifacts: InvestigationExecutionArtifactSummary[];
  result_page: InvestigationExecutionArtifactPage | null;
}
