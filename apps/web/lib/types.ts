// Shared view-layer types. These mirror the backend SQLAlchemy models
// (alerts / analyses / application_* / experiences) so the UI can be wired to
// real API responses later without changing component contracts.

export type Level = 'CRITICAL' | 'WARNING';

export type AnalysisStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'needs_review'
  | 'failed'
  | 'needs_human';

export type StepStatus = 'done' | 'running' | 'pending' | 'degraded' | 'failed' | 'skipped';

export interface Application {
  id: string;
  name: string;
  topic: string;
  level: Level;
  repoCount: number;
  ingestionState: 'draft' | 'active' | 'paused';
  ingestionObservedState: 'draft' | 'starting' | 'listening' | 'paused' | 'error';
  ingestionStartPosition: 'earliest' | 'latest' | null;
  myPerm: string | null;
  createdAt: string;
}

export interface Analysis {
  id: string;
  dedupeKey: string;
  applicationId: string;
  applicationName: string;
  title: string;
  level: Level;
  status: AnalysisStatus;
  confidence: number | null;
  conclusion: string | null;
  /** Caller's permission on this analysis's application (undefined for admins). */
  myPerm?: string;
}

export interface AnalysisStep {
  nodeType:
    | 'receive'
    | 'git_sync'
    | 'context'
    | 'service_snapshot'
    | 'ai_analysis'
    | 'experience'
    | 'conclusion';
  status: StepStatus;
  summary?: string;
  detail?: string;
  startedAt?: string;
  finishedAt?: string;
}

export interface AnalysisRecommendation {
  id: number;
  summary: string;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  basis: 'evidence_backed' | 'safety_fallback';
  evidence_refs: number[];
  preconditions: string[];
  steps: { action: string; expected_result: string }[];
  verification: string[];
  rollback: string[];
  owner_role: string | null;
  prompt_markdown: string;
  engine_version: string | null;
  created_at: string;
}

export interface AnalysisFeedbackSummary {
  remediation_useful: number;
  remediation_not_useful: number;
  agent_prompt_useful: number;
  agent_prompt_not_useful: number;
  my_remediation: 'useful' | 'not_useful' | null;
  my_agent_prompt: 'useful' | 'not_useful' | null;
}

export interface Experience {
  id: string;
  applicationName?: string;
  triggerSignature: string;
  content: string;
  valid: boolean;
}

export interface CurrentUser {
  id: number;
  email: string;
  name: string;
  role: string;
  status: string;
  created_at: string;
}

export interface AiModelConfig {
  id: number;
  provider: string;
  base_url: string;
  model: string;
  is_default: boolean;
  has_key: boolean;
}

export interface Invite {
  id: number;
  email: string;
  token: string;
  status: string;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  actor_id: number | null;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  application_id: number | null;
  request_id: string | null;
  trace_id: string | null;
  result: 'ok' | 'error';
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditEventList {
  total: number;
  limit: number;
  offset: number;
  items: AuditEvent[];
}

export interface DeadLetter {
  id: number;
  kind: string;
  topic: string;
  dedupe_key: string | null;
  payload: Record<string, unknown> | null;
  reason: string | null;
  replayed: boolean;
  created_at: string;
}

export interface ReplayOut {
  id: number;
  topic: string;
  status: string;
}
