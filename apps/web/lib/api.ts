// Thin client for the Lode backend API.
//
// The backend speaks snake_case and uses DB-native statuses
// (queued/running/completed/failed). Result maturity is a separate contract.
// All mapping happens here so page components stay presentational.

import type {
  AiModelConfig,
  Application,
  AuditEvent,
  AuditEventList,
  CurrentUser,
  DeadLetter,
  ReplayOut,
  Invite,
  Level,
} from '@/lib/types';

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

// The session JWT lives in a cookie (not localStorage) so the auth gate in
// `middleware.ts` can read it on the server and redirect unauthenticated
// requests *before* any HTML is sent — eliminating the client-side flash-of-white.
// It is a readable (non-HttpOnly) cookie because the cross-origin FastAPI backend
// is authorized via the `Authorization: Bearer` header (read here), not cookies.
const TOKEN_KEY = 'lode_token';
export const SESSION_EXPIRED_EVENT = 'lode:session-expired';

export function getToken(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(
    new RegExp('(^|;\\s*)' + TOKEN_KEY + '=([^;]*)')
  );
  return match ? decodeURIComponent(match[2]) : null;
}

export function setToken(token: string): void {
  if (typeof document === 'undefined') return;
  const maxAge = tokenMaxAgeSeconds(token);
  const secure =
    typeof window !== 'undefined' && window.location.protocol === 'https:'
      ? '; Secure'
      : '';
  document.cookie = `${TOKEN_KEY}=${encodeURIComponent(token)}; Path=/; Max-Age=${maxAge}; SameSite=Lax${secure}`;
}

export function clearToken(): void {
  if (typeof document === 'undefined') return;
  document.cookie = `${TOKEN_KEY}=; Path=/; Max-Age=0; SameSite=Lax`;
}

function rejectExpiredSession(): never {
  clearToken();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  throw new Error('unauthorized');
}

function assertAuthenticated(response: Response): void {
  if (response.status === 401) rejectExpiredSession();
}

async function responseErrorMessage(response: Response, fallback: string): Promise<string> {
  const body: unknown = await response.json().catch(() => null);
  if (!body || typeof body !== 'object') return fallback;

  const payload = body as {
    error?: { message?: unknown };
    detail?: unknown;
  };
  if (typeof payload.error?.message === 'string' && payload.error.message.trim()) {
    return payload.error.message;
  }
  if (typeof payload.detail === 'string' && payload.detail.trim()) {
    return payload.detail;
  }
  if (Array.isArray(payload.detail)) {
    const messages = payload.detail
      .map((item) => (
        item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string'
          ? (item as { msg: string }).msg
          : null
      ))
      .filter((item): item is string => Boolean(item));
    if (messages.length) return messages.join('; ');
  }
  if (payload.detail && typeof payload.detail === 'object') {
    const detail = payload.detail as { message?: unknown; missing?: unknown };
    if (typeof detail.message === 'string' && detail.message.trim()) {
      return detail.message;
    }
  }
  return fallback;
}

// Derive cookie lifetime from the JWT `exp` claim so the cookie expires with the
// token. Falls back to 7 days if the claim is missing or unreadable.
function tokenMaxAgeSeconds(token: string): number {
  const exp = jwtExp(token);
  if (typeof exp === 'number') {
    return Math.max(0, exp - Math.floor(Date.now() / 1000));
  }
  return 60 * 60 * 24 * 7;
}

// Decode the JWT `exp` claim. Returns undefined on any parse failure — callers
// treat that as "not valid" (fail-safe) rather than trusting an unreadable token.
function jwtExp(token: string): number | undefined {
  try {
    const payload = JSON.parse(base64UrlDecode(token.split('.')[1] ?? ''));
    return typeof payload?.exp === 'number' ? payload.exp : undefined;
  } catch {
    return undefined;
  }
}

function base64UrlDecode(input: string): string {
  const b64 = input.replace(/-/g, '+').replace(/_/g, '/');
  if (typeof atob === 'function') return atob(b64);
  // Node fallback (SSR/tests) — never reached in the browser.
  return Buffer.from(b64, 'base64').toString('utf-8');
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function apiFetch(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new Error(`network error: ${API_BASE}${path}`);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const res = await apiFetch(path, { cache: 'no-store', headers: authHeaders() });
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `request failed: ${res.status} ${path}`));
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Shapes returned by the API (snake_case)
// ---------------------------------------------------------------------------

interface ApiApplication {
  id: number;
  name: string;
  topic: string | null;
  latest_level: string;
  repo_count: number;
  model_configured: boolean;
  model_available: boolean;
  ingestion_state: 'draft' | 'active' | 'paused';
  ingestion_observed_state: 'draft' | 'starting' | 'listening' | 'paused' | 'error';
  ingestion_start_position: 'earliest' | 'latest' | null;
  my_perm: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Public client functions
// ---------------------------------------------------------------------------

export type InvestigationStatus = 'queued' | 'running' | 'completed' | 'failed';
export type InvestigationResultState = 'pending' | 'confirmed' | 'hypothesis' | 'insufficient' | 'unavailable';
export type InvestigationStepStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'blocked' | 'failed' | 'canceled';
export interface InvestigationSummary { id: string; application_id: number; application_name: string; title: string; level: string; status: InvestigationStatus; result_state: InvestigationResultState; review_required: boolean; archived_at: string | null; retry_of: number | null; created_at: string; }
export interface InvestigationEvidence { id: number; type: string; source: string; locator: string | null; content_hash: string; excerpt: string; metadata: Record<string, unknown>; collected_at: string; code?: { language: string; content: string; highlight_start: number; highlight_end: number; anchor: { repo_id: number | null; path: string; revision: string; revision_role: 'incident' | 'latest'; symbol: string | null; start_line: number; end_line: number } }; }
export interface InvestigationOperationEvent { sequence: number; kind: 'started' | 'progress' | 'finished'; message: string; detail: Record<string, unknown>; evidence_refs: number[]; occurred_at: string; }
export interface InvestigationOperation { id: string; step_id: number; ordinal: number; kind: string; actor: 'engine' | 'ai' | 'collector'; title: string; purpose: string; input: Record<string, unknown>; status: InvestigationStepStatus; result: string; metrics: Record<string, unknown>; evidence_refs: number[]; failure: { code: string; detail: string | null } | null; started_at: string | null; finished_at: string | null; duration_ms: number | null; events: InvestigationOperationEvent[]; }
export interface InvestigationStep { id: string; db_id: number; ordinal: number; kind: string; title: string; objective: string; selection_reason: string; expected_evidence: string; tool_name: string | null; tool_input: Record<string, unknown>; status: InvestigationStepStatus; input_refs: number[]; output_refs: number[]; result: string; failure: { code: string; detail: string | null } | null; started_at: string | null; finished_at: string | null; duration_ms: number | null; }
export interface InvestigationCodeFinding { id: number; artifact_id: number | null; status: 'confirmed' | 'hypothesis' | 'no_defect' | 'not_found'; repo_id: number | null; revision: string | null; revision_role: 'incident' | 'latest' | null; path: string | null; symbol: string | null; start_line: number | null; end_line: number | null; issue_type: string | null; faulty_behavior: string; why_wrong: string; expected_behavior: string; trigger_condition: string; causal_chain: string[]; incident_evidence_refs: number[]; supporting_evidence_refs: number[]; counter_evidence_refs: number[]; missing_validation: string[]; fix_direction: string; test_scenario: string; created_at: string; }
export interface InvestigationReport { result_state: Exclude<InvestigationResultState, 'pending'>; headline: string; summary: string; incident_cause: { status: string; mechanism: string; why: string; causal_chain: string[]; evidence_refs: number[] }; code_diagnosis: { status: string; summary: string; findings: unknown[] }; confirmed_facts: { text: string; evidence_refs: number[] }[]; counter_evidence: ({ text: string; evidence_refs: number[] } | string)[]; evidence_gaps: string[]; next_step: { type?: string; text?: string }; evidence_refs: number[]; }
export interface InvestigationDetail { id: string; application_id: number; application_name: string; status: InvestigationStatus; result_state: InvestigationResultState; output_language: 'en' | 'zh'; scope: { service: string | null; environment: string | null; trace_id: string | null; deployment_sha: string | null; sources: Record<string, string | null>; window_started_at: string; window_finished_at: string }; review_required: boolean; review_reasons: string[]; engine_version: string | null; created_at: string; started_at: string | null; finished_at: string | null; retry_of: string | null; archived_at: string | null; archived_by: number | null; input: { source_type: string; title: string; severity: string; occurred_at: string; error: { name: string; message: string; stack: string | null; cause: unknown; properties: Record<string, unknown> }; fields: Record<string, unknown> } | null; report: InvestigationReport | null; steps: InvestigationStep[]; decisions: { id: number; ordinal: number; after_step_id: number | null; action: string; selected_tool: string | null; rationale: string; hypothesis: Record<string, unknown>; evidence_refs: number[]; created_at: string }[]; operations: InvestigationOperation[]; evidence: InvestigationEvidence[]; code_findings: InvestigationCodeFinding[]; event_cursor: number; }
export interface InvestigationLiveEvent { type: string; sequence?: number; payload: Record<string, unknown>; }
export interface InvestigationCreateInput { application_id: number; title: string; severity: 'CRITICAL' | 'WARNING'; occurred_at: string; error: { name: string; message: string; stack?: string; cause?: unknown; properties?: Record<string, unknown> }; service_name?: string; environment?: string; trace_id?: string; deployment_sha?: string; fields?: Record<string, unknown>; attachments?: { kind: 'log' | 'trace' | 'dependency' | 'gateway_response'; label: string; content: string }[]; }
export interface InvestigationAuditCall { id: number; step_id: number | null; purpose: string; provider: string | null; model: string | null; status: string; prompt_template_version: string; input_hash: string; output_hash: string | null; latency_ms: number; input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; token_source: string; error_code: string | null; error_detail: string | null; attempt_count: number; summary: string; evidence_refs: number[]; created_at: string; }
export interface InvestigationAuditPage { operations: { items: InvestigationOperation[]; next_cursor: number | null }; ai_calls: { items: InvestigationAuditCall[]; next_cursor: number | null }; }

export async function fetchInvestigations(): Promise<InvestigationSummary[]> { return getJson('/investigations'); }
export async function fetchInvestigation(id: string): Promise<InvestigationDetail> { return getJson('/investigations/' + encodeURIComponent(id)); }
export async function createInvestigation(body: InvestigationCreateInput): Promise<{ id: string; job_id: string; status: 'queued' }> { return postJson('/investigations', body); }
export async function retryInvestigation(id: string): Promise<{ id: string; job_id: string; status: 'queued'; retry_of: string }> { return postJson(`/investigations/${encodeURIComponent(id)}/retry`, {}); }
export async function archiveInvestigation(id: string): Promise<{ id: string; archived_at: string; read_only: true }> { return postJson(`/investigations/${encodeURIComponent(id)}/archive`, {}); }
export async function fetchInvestigationAudit(id: string, operationCursor = 0, aiCursor = 0): Promise<InvestigationAuditPage> { return getJson(`/investigations/${encodeURIComponent(id)}/audit?operation_cursor=${operationCursor}&ai_cursor=${aiCursor}&limit=50`); }

export function openInvestigationStream(id: string, after: number, handlers: { onEvent: (event: InvestigationLiveEvent) => void; onClose: () => void; onError: (message: string) => void }): () => void {
  const controller = new AbortController();
  void (async () => {
    try {
      const response = await apiFetch('/investigations/' + encodeURIComponent(id) + '/stream?after=' + encodeURIComponent(String(after)), { headers: authHeaders(), signal: controller.signal, cache: 'no-store' });
      assertAuthenticated(response);
      if (!response.ok || !response.body) throw new Error(await responseErrorMessage(response, '实时调查连接失败'));
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (!controller.signal.aborted) {
        const next = await reader.read();
        if (next.done) break;
        buffer += decoder.decode(next.value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() ?? '';
        for (const frame of frames) {
          const lines = frame.split(/\r?\n/);
          const eventName = lines.find((line) => line.startsWith('event:'))?.slice(6).trim();
          const body = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
          if (!eventName || !body) continue;
          const payload: unknown = JSON.parse(body);
          if (payload && typeof payload === 'object') handlers.onEvent({ type: eventName, sequence: typeof (payload as { sequence?: unknown }).sequence === 'number' ? (payload as { sequence: number }).sequence : undefined, payload: payload as Record<string, unknown> });
        }
      }
      if (!controller.signal.aborted) handlers.onClose();
    } catch (cause) {
      if (!controller.signal.aborted) handlers.onError(String(cause));
    }
  })();
  return () => controller.abort();
}


export interface LoginResult {
  token: string;
  user: CurrentUser;
}

export async function login(email: string, password: string): Promise<LoginResult> {
  const res = await apiFetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, 'login failed'));
  }
  return (await res.json()) as LoginResult;
}

export async function fetchApplications(): Promise<Application[]> {
  const rows = await getJson<ApiApplication[]>('/applications');
  return rows.map((r) => ({
    id: String(r.id),
    name: r.name,
    topic: r.topic ?? '',
    level: (r.latest_level as Level) ?? 'WARNING',
    repoCount: r.repo_count,
    modelConfigured: r.model_configured,
    modelAvailable: r.model_available,
    ingestionState: r.ingestion_state,
    ingestionObservedState: r.ingestion_observed_state,
    ingestionStartPosition: r.ingestion_start_position,
    myPerm: r.my_perm,
    createdAt: r.created_at,
  }));
}

export async function fetchApplication(id: string): Promise<{
  id: number;
  name: string;
  topic: string | null;
  model_config_id: number | null;
  ingestion_state: 'draft' | 'active' | 'paused';
  my_perm: string | null;
  repos: {
    id: number;
    repo_id: number;
    name: string;
    url: string;
    scope: string;
    repo_type: string;
    default_branch: string;
    description: string;
  }[];
  descriptions: { id: number; description_type: string; content: string }[];
  db_sources: DbSourceRow[];
  integrations: ApplicationIntegrationRow[];
}> {
  return getJson(`/applications/${id}`);
}

export interface IngestionStatus {
  application_id: number;
  topic: string | null;
  desired_state: 'draft' | 'active' | 'paused';
  observed_state: 'draft' | 'starting' | 'listening' | 'paused' | 'error';
  ingestion_version: number;
  start_position: 'earliest' | 'latest' | null;
  assigned_partitions: number;
  backlog: number | null;
  last_heartbeat_at: string | null;
  last_error: string | null;
}

export async function fetchApplicationIngestion(id: string | number): Promise<IngestionStatus> {
  return getJson(`/applications/${id}/ingestion`);
}

export async function startApplicationIngestion(
  id: string | number,
  startPosition: 'earliest' | 'latest'
): Promise<IngestionStatus> {
  return postJson(`/applications/${id}/ingestion/start`, { start_position: startPosition });
}

export async function pauseApplicationIngestion(id: string | number): Promise<IngestionStatus> {
  return postJson(`/applications/${id}/ingestion/pause`, {});
}

export async function resumeApplicationIngestion(id: string | number): Promise<IngestionStatus> {
  return postJson(`/applications/${id}/ingestion/resume`, {});
}

// ---------------------------------------------------------------------------
// Per-application admin writes
// ---------------------------------------------------------------------------
//
// These back the per-application Settings tabs (Kafka topic, repos, descriptions,
// data sources). Every call requires the caller to be an admin; the backend
// enforces this with ``Depends(require_admin)`` and the 403 surfaces here as a
// thrown ``Error``.

export async function setApplicationTopic(
  applicationId: string | number,
  topic: string | null
): Promise<{ application_id: number; topic: string | null }> {
  return putJson(`/applications/${applicationId}/topic`, {
    topic: topic && topic.trim() ? topic.trim() : null,
  });
}

export interface BindRepoInput {
  repo_id: number;
  description: string;
}

export interface ApplicationRepoRow {
  id: number;
  application_id: number;
  repo_id: number;
  repo_name: string;
  repo_url: string;
  repo_scope: string;
  repo_type: string;
  default_branch: string;
  description: string;
}

export async function bindRepo(
  applicationId: string | number,
  input: BindRepoInput
): Promise<ApplicationRepoRow> {
  return postJson(`/applications/${applicationId}/repos`, input);
}

export interface CreateLocalRepoInput {
  name: string;
  repo_url: string;
  default_branch: string;
  repo_type: string;
  credential_id: number | null;
  description: string;
}

export async function createLocalRepo(
  applicationId: string | number,
  input: CreateLocalRepoInput
): Promise<ApplicationRepoRow> {
  return postJson(`/applications/${applicationId}/repos/local`, input);
}

export async function unbindRepo(
  applicationId: string | number,
  repoId: number
): Promise<void> {
  const res = await apiFetch(
    `/applications/${applicationId}/repos/${repoId}`,
    {
      method: 'DELETE',
      headers: authHeaders(),
    }
  );
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `unbind repo failed: ${res.status}`));
  }
}

export interface CreateDbSourceInput {
  name: string;
  description: string;
  // Mode 1: structured connection fields (built into a DSN at query time).
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  password?: string;
  // Mode 2: secret reference (env:// / bare DSN). Either this or the
  // structured fields must be supplied.
  conn_secret_ref?: string;
  sslmode?: string | null;
  allowed_tables: string[];
  sensitive_columns: string[];
}

export interface UpdateDbSourceInput {
  name?: string;
  description?: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  password?: string;
  conn_secret_ref?: string;
  sslmode?: string | null;
  allowed_tables?: string[];
  sensitive_columns?: string[];
}

export interface DbSourceRow {
  id: number;
  application_id: number;
  name: string;
  description: string;
  conn_secret_ref: string | null;
  host: string | null;
  port: number | null;
  database: string | null;
  username: string | null;
  has_password: boolean;
  sslmode: string | null;
  allowed_tables: string[];
  sensitive_columns: string[];
}

export interface ApplicationIntegrationRow {
  id: number;
  application_id: number;
  name: string;
  kind: 'redis' | 'kafka' | 'clickhouse';
    state: 'active' | 'disabled';
  readonly_verified_at: string | null;
  last_collected_at: string | null;
  last_error: string | null;
}

export interface ApplicationIntegrationInput {
    name: string;
    kind: 'redis' | 'kafka' | 'clickhouse';
    config: Record<string, unknown>;
    secret_ref: string;
}

export interface ApplicationIntegrationConfiguration extends ApplicationIntegrationRow {
  config: Record<string, unknown>;
}

export async function testApplicationIntegration(applicationId: string | number, input: ApplicationIntegrationInput): Promise<{ ok: boolean; error: string | null }> {
  return postJson(`/applications/${applicationId}/integrations/test`, input);
}

export async function createApplicationIntegration(applicationId: string | number, input: ApplicationIntegrationInput): Promise<ApplicationIntegrationRow> {
  return postJson(`/applications/${applicationId}/integrations`, input);
}

export async function getApplicationIntegration(applicationId: string | number, integrationId: number): Promise<ApplicationIntegrationConfiguration> {
  return getJson(`/applications/${applicationId}/integrations/${integrationId}`);
}

export async function updateApplicationIntegration(applicationId: string | number, integrationId: number, input: Partial<ApplicationIntegrationInput> & { state?: 'active' | 'disabled' }): Promise<ApplicationIntegrationRow> {
  return putJson(`/applications/${applicationId}/integrations/${integrationId}`, input);
}

export async function deleteApplicationIntegration(applicationId: string | number, integrationId: number): Promise<void> {
  const res = await apiFetch(`/applications/${applicationId}/integrations/${integrationId}`, { method: 'DELETE', headers: authHeaders() });
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `delete integration failed: ${res.status}`));
  }
}

export async function createDbSource(
  applicationId: string | number,
  input: CreateDbSourceInput
): Promise<DbSourceRow> {
  return postJson(`/applications/${applicationId}/db-sources`, input);
}

export async function updateDbSource(
  applicationId: string | number,
  sourceId: number,
  input: UpdateDbSourceInput
): Promise<DbSourceRow> {
  return putJson(`/applications/${applicationId}/db-sources/${sourceId}`, input);
}

export async function testDbSource(
  applicationId: string | number,
  input: CreateDbSourceInput
): Promise<{ ok: boolean; latency_ms: number | null; error: string | null }> {
  return postJson(`/applications/${applicationId}/db-sources/test`, input);
}

export async function deleteDbSource(
  applicationId: string | number,
  sourceId: number
): Promise<void> {
  const res = await apiFetch(
    `/applications/${applicationId}/db-sources/${sourceId}`,
    { method: 'DELETE', headers: authHeaders() }
  );
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `delete data source failed: ${res.status}`));
  }
}

// ---------------------------------------------------------------------------
// Read-only query console
// ---------------------------------------------------------------------------

export interface QueryResult {
  source_id?: number;
  source_name?: string;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  truncated: boolean;
  desensitized: boolean;
  allowed_tables: string[];
}

export interface RunQueryInput {
  source_id: number;
  table: string;
  operation: 'sample' | 'count';
}

export async function executeQuery(
  applicationId: string | number,
  input: RunQueryInput
): Promise<QueryResult> {
  return postJson<QueryResult>(`/applications/${applicationId}/query`, input);
}

export async function setApplicationModel(
  applicationId: string | number,
  modelConfigId: number | null
): Promise<{ application_id: number; model_config_id: number | null; model_test: ModelAvailability | null }> {
  return putJson(`/applications/${applicationId}/model`, {
    model_config_id: modelConfigId,
  });
}

export interface CreateApplicationDescriptionInput {
  description_type: 'deploy' | 'other';
  content: string;
}

export interface ApplicationDescriptionRow {
  id: number;
  application_id: number;
  description_type: string;
  content: string;
}

export async function createApplicationDescription(
  applicationId: string | number,
  input: CreateApplicationDescriptionInput
): Promise<ApplicationDescriptionRow> {
  return postJson(`/applications/${applicationId}/descriptions`, input);
}

export async function deleteApplicationDescription(
  applicationId: string | number,
  descriptionId: number
): Promise<void> {
  const res = await apiFetch(
    `/applications/${applicationId}/descriptions/${descriptionId}`,
    { method: 'DELETE', headers: authHeaders() }
  );
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `delete description failed: ${res.status}`));
  }
}

export interface CreateApplicationInput {
  name: string;
}

export async function createApplication(input: CreateApplicationInput): Promise<Application> {
  const row = await postJson<ApiApplication>('/applications', input);
  return {
    id: String(row.id),
    name: row.name,
    topic: row.topic ?? '',
    level: (row.latest_level as Level) ?? 'WARNING',
    repoCount: row.repo_count,
    modelConfigured: row.model_configured,
    modelAvailable: row.model_available,
    ingestionState: row.ingestion_state,
    ingestionObservedState: row.ingestion_observed_state,
    ingestionStartPosition: row.ingestion_start_position,
    myPerm: row.my_perm,
    createdAt: row.created_at,
  };
}

// ---------------------------------------------------------------------------
// Application membership (admin / app-admin)
// ---------------------------------------------------------------------------

export type AppPerm = 'read' | 'analyze' | 'admin';

export interface AppMember {
  user_id: number;
  email: string;
  name: string;
  role: string;
  status: string;
  perm: AppPerm;
}

export async function fetchAppMembers(appId: string | number): Promise<AppMember[]> {
  return getJson<AppMember[]>(`/applications/${appId}/members`);
}

export async function fetchAppMemberCandidates(appId: string | number): Promise<CurrentUser[]> {
  return getJson<CurrentUser[]>(`/applications/${appId}/member-candidates`);
}

export async function addAppMember(
  appId: string | number,
  userId: number,
  perm: AppPerm
): Promise<AppMember> {
  return postJson<AppMember>(`/applications/${appId}/members`, {
    user_id: userId,
    perm,
  });
}

export async function updateAppMember(
  appId: string | number,
  userId: number,
  perm: AppPerm
): Promise<AppMember> {
  return putJson<AppMember>(`/applications/${appId}/members/${userId}`, { perm });
}

export async function removeAppMember(
  appId: string | number,
  userId: number
): Promise<void> {
  const res = await apiFetch(
    `/applications/${appId}/members/${userId}`,
    { method: 'DELETE', headers: authHeaders() }
  );
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `remove member failed: ${res.status}`));
  }
}

export interface GlobalSettings {
  ai_output_language: 'en' | 'zh';
  supported_ai_output_languages: ('en' | 'zh')[];
  git_credentials: { id: number; auth_type: string; username: string; readonly: boolean; note: string; has_secret: boolean }[];
  git_repos: { id: number; name: string; repo_url: string; default_branch: string; scope: string; application_id: number | null; repo_type: string; credential_id: number | null }[];
  ai_model_configs: {
    id: number;
    provider: string;
    base_url: string;
    model: string;
    is_default: boolean;
    has_key: boolean;
    last_test_status: 'untested' | 'available' | 'unavailable';
    last_tested_at: string | null;
    last_test_latency_ms: number | null;
    last_test_error_code: string | null;
    last_test_error_detail: string | null;
  }[];
}

export async function fetchSettings(): Promise<GlobalSettings> {
  return getJson<GlobalSettings>('/settings');
}

export async function updateAiOutputLanguage(
  language: GlobalSettings['ai_output_language']
): Promise<{ language: GlobalSettings['ai_output_language'] }> {
  return putJson('/settings/ai-output-language', { language });
}

export interface AiModelInput {
  provider: string;
  base_url: string;
  api_key_ref: string;
  model: string;
  is_default: boolean;
}

export interface ModelAvailability {
  available: boolean;
  endpoint: string;
  latency_ms: number;
  error_code: string | null;
  error_detail: string | null;
}

export async function createAiModel(input: AiModelInput): Promise<AiModelConfig> {
  return postJson<AiModelConfig>('/settings/ai-models', input);
}

export async function fetchAiModelConfigs(): Promise<AiModelConfig[]> {
  return getJson<AiModelConfig[]>('/settings/ai-models');
}

export async function testAiModel(id: number): Promise<ModelAvailability> {
  return postJson<ModelAvailability>(`/settings/ai-models/${id}/test`, {});
}

export async function updateAiModel(id: number, input: AiModelInput): Promise<AiModelConfig> {
  return putJson<AiModelConfig>(`/settings/ai-models/${id}`, input);
}

export async function deleteAiModel(id: number): Promise<void> {
  const res = await apiFetch(`/settings/ai-models/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  assertAuthenticated(res);
  if (!res.ok) throw new Error(await responseErrorMessage(res, `delete ai model failed: ${res.status}`));
}

// ---------------------------------------------------------------------------
// Git credentials (admin)
// ---------------------------------------------------------------------------

export interface GitCredentialInput {
  auth_type: string;
  username: string;
  secret_ref: string;
  readonly: boolean;
  note: string;
}

export interface GitCredentialRow {
  id: number;
  auth_type: string;
  username: string;
  readonly: boolean;
  note: string;
  has_secret: boolean;
}

export async function createGitCredential(input: GitCredentialInput): Promise<GitCredentialRow> {
  return postJson<GitCredentialRow>('/settings/git-credentials', input);
}

export async function updateGitCredential(id: number, input: Partial<GitCredentialInput>): Promise<GitCredentialRow> {
  return putJson<GitCredentialRow>(`/settings/git-credentials/${id}`, input);
}

export async function deleteGitCredential(id: number): Promise<void> {
  const res = await apiFetch(`/settings/git-credentials/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  assertAuthenticated(res);
  if (!res.ok) throw new Error(await responseErrorMessage(res, `delete git credential failed: ${res.status}`));
}

// ---------------------------------------------------------------------------
// Git repository registry (admin)
// ---------------------------------------------------------------------------

export interface GitRepoInput {
  name: string;
  repo_url: string;
  default_branch: string;
  repo_type: string;
  credential_id: number | null;
}

export interface GitRepoRow {
  id: number;
  name: string;
  repo_url: string;
  default_branch: string;
  scope: string;
  application_id: number | null;
  repo_type: string;
  credential_id: number | null;
}

export async function createGitRepo(input: GitRepoInput): Promise<GitRepoRow> {
  return postJson<GitRepoRow>('/settings/git-repos', input);
}

export async function updateGitRepo(id: number, input: Partial<GitRepoInput>): Promise<GitRepoRow> {
  return putJson<GitRepoRow>(`/settings/git-repos/${id}`, input);
}

export async function deleteGitRepo(id: number): Promise<void> {
  const res = await apiFetch(`/settings/git-repos/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  assertAuthenticated(res);
  if (!res.ok) throw new Error(await responseErrorMessage(res, `delete git repo failed: ${res.status}`));
}

// ---------------------------------------------------------------------------
// Current user / account
// ---------------------------------------------------------------------------

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return getJson<CurrentUser>('/auth/me');
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  const res = await apiFetch('/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `change password failed: ${res.status}`));
  }
}

// ---------------------------------------------------------------------------
// User administration (admin)
// ---------------------------------------------------------------------------

export interface UserInput {
  email?: string;
  name?: string;
  role?: string;
  password?: string;
  status?: string;
}

export async function fetchUsers(): Promise<CurrentUser[]> {
  return getJson<CurrentUser[]>('/users');
}

export async function createUser(input: UserInput): Promise<CurrentUser> {
  return postJson<CurrentUser>('/users', input);
}

export async function updateUser(id: number, input: UserInput): Promise<CurrentUser> {
  return putJson<CurrentUser>(`/users/${id}`, input);
}

export async function deleteUser(id: number): Promise<void> {
  const res = await apiFetch(`/users/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `delete user failed: ${res.status}`));
  }
}

export async function resetUserPassword(id: number, password: string): Promise<void> {
  const res = await apiFetch(`/users/${id}/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ password }),
  });
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `reset password failed: ${res.status}`));
  }
}

// ---------------------------------------------------------------------------
// Invitations (admin create/list, open accept)
// ---------------------------------------------------------------------------

export async function createInvite(email: string): Promise<Invite> {
  return postJson<Invite>('/invites', { email });
}

export async function fetchInvites(): Promise<Invite[]> {
  return getJson<Invite[]>('/invites');
}

export async function acceptInvite(token: string, password: string, name: string): Promise<void> {
  const res = await apiFetch('/invites/accept', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, password, name }),
  });
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `accept invite failed: ${res.status}`));
  }
}

// ---------------------------------------------------------------------------
// Audit log (admin read)
// ---------------------------------------------------------------------------

export interface AuditQuery {
  action?: string;
  actor_id?: number;
  actor_email?: string;
  target_type?: string;
  target_id?: string;
  application_id?: number;
  result?: 'ok' | 'error';
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export async function fetchAuditEvents(query: AuditQuery = {}): Promise<AuditEventList> {
  const params = new URLSearchParams();
  (Object.entries(query) as [string, unknown][]).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v));
  });
  const qs = params.toString();
  return getJson<AuditEventList>(`/audit${qs ? `?${qs}` : ''}`);
}

// ---------------------------------------------------------------------------
// Dead-letter queue (admin read + replay)
// ---------------------------------------------------------------------------
//
// These back the admin Dead Letters console. `list` reads rejected messages
// (parse failures / schema errors / unmapped topics); `replay` re-injects a
// message onto its source Kafka topic so the consumer re-processes it. Both are
// admin-only on the backend (`require_admin`).

export async function fetchDeadLetters(
  kind?: 'dlq' | 'unassigned'
): Promise<DeadLetter[]> {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : '';
  return getJson<DeadLetter[]>(`/dead-letters${qs}`);
}

export async function replayDeadLetter(id: number): Promise<ReplayOut> {
  const res = await apiFetch(`/dead-letters/${id}/replay`, {
    method: 'POST',
    headers: authHeaders(),
  });
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `replay failed: ${res.status}`));
  }
  return (await res.json()) as ReplayOut;
}

// ---------------------------------------------------------------------------
// Private JSON helpers (POST/PUT with auth)
// ---------------------------------------------------------------------------

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `request failed: ${res.status} ${path}`));
  }
  return (await res.json()) as T;
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  assertAuthenticated(res);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res, `request failed: ${res.status} ${path}`));
  }
  return (await res.json()) as T;
}
