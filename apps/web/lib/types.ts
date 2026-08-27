export interface CurrentUser {
  id: number;
  email: string;
  name: string;
  role: 'admin' | 'user';
  status: 'pending' | 'active' | 'disabled';
  created_at: string;
}

export interface Invite {
  id: number;
  email: string;
  token: string;
  status: string;
  created_at: string;
}

export interface Workspace {
  id: number;
  name: string;
  ingestion_topic: string;
  model_policy_revision_id: number | null;
  ingestion_state: 'draft' | 'active' | 'paused';
  ingestion_version: number;
  ingestion_start_position: 'earliest' | 'latest' | null;
  ingestion_started_at: string | null;
  ingestion_paused_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProviderAccount {
  id: number;
  name: string;
  provider_kind: 'openai' | 'openai_compatible' | 'anthropic';
  base_url: string;
  state: 'active' | 'disabled';
  verification_status: 'untested' | 'healthy' | 'unavailable';
  verified_at: string | null;
  revision: number;
}

export interface ModelDeployment {
  id: number;
  provider_account_id: number;
  provider_model_id: string;
  display_name: string;
  capabilities: Record<string, unknown>;
  max_input_tokens: number;
  max_output_tokens: number;
  tokenizer_id: string;
  availability_state: 'untested' | 'healthy' | 'unavailable';
  state: 'active' | 'disabled';
  revision: number;
}

export interface ModelBinding {
  id: number;
  workspace_id: number;
  model_deployment_id: number;
  execution_classes: string[];
  allowed_roles: string[];
  priority: number;
  max_calls: number;
  max_input_tokens: number;
  max_output_tokens: number;
  max_cost_per_call: number;
  timeout_ms: number;
  allowed_data_classes: string[];
  max_context_utilization: number;
  state: 'active' | 'disabled';
  revision: number;
}

export interface RepositoryBinding {
  id: number;
  workspace_id: number;
  repository_id: number;
  name: string;
  repo_url: string;
  repo_type: string;
  default_branch: string;
  role: string;
  priority: number;
  description: string;
  state: 'active' | 'disabled';
  revision: number;
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

export interface InvestigationSummary {
  id: number;
  public_id: string;
  workspace_id: number;
  status: 'queued' | 'running' | 'completed' | 'failed';
  result_state: 'pending' | 'confirmed' | 'hypothesis' | 'insufficient' | 'unavailable';
  retry_of_id: number | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvestigationDetail {
  investigation: InvestigationSummary & Record<string, unknown>;
  input: {
    source_type: string;
    event: string;
    severity: 'CRITICAL' | 'WARNING';
    occurred_at: string;
    source_revision: string | null;
    error: Record<string, unknown>;
    attachments_masked: Array<Record<string, unknown>>;
  } | null;
  snapshot_summary: Record<string, unknown>;
  context_revisions: Array<Record<string, unknown>>;
  model_routing: Array<Record<string, unknown>>;
  steps: Array<Record<string, unknown>>;
  decisions: Array<Record<string, unknown>>;
  operations: Array<Record<string, unknown>>;
  evidence: {
    collections: Array<Record<string, unknown>>;
    artifacts: Array<Record<string, unknown>>;
    assertions: Array<Record<string, unknown>>;
    entities: Array<Record<string, unknown>>;
    events: Array<Record<string, unknown>>;
    relations: Array<Record<string, unknown>>;
  };
  source_revisions: Array<Record<string, unknown>>;
  source_assessments: Array<Record<string, unknown>>;
  code_findings: Array<Record<string, unknown>>;
  report: Record<string, unknown> | null;
}
