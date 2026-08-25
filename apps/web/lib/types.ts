// Shared view-layer types for application, identity, and administrative APIs.

export type Level = 'CRITICAL' | 'WARNING';

export interface Application {
  id: string;
  name: string;
  topic: string;
  level: Level;
  repoCount: number;
  modelConfigured: boolean;
  ingestionState: 'draft' | 'active' | 'paused';
  ingestionObservedState: 'draft' | 'starting' | 'listening' | 'paused' | 'error';
  ingestionStartPosition: 'earliest' | 'latest' | null;
  myPerm: string | null;
  createdAt: string;
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
