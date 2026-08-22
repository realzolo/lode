// Thin client for the Lode backend API.
//
// The backend speaks snake_case and uses DB-native statuses
// (pending/running/completed/failed/canceled). The UI expects camelCase and
// a slightly wider status set (it adds `needs_human`). All mapping happens
// here so the page components stay presentational.

import type {
  Analysis,
  AnalysisStatus,
  AnalysisStep,
  AiModelConfig,
  Application,
  CurrentUser,
  Invite,
  Level,
  Memory,
  StepStatus,
} from '@/lib/types';

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

// The session JWT lives in a cookie (not localStorage) so the auth gate in
// `middleware.ts` can read it on the server and redirect unauthenticated
// requests *before* any HTML is sent — eliminating the client-side flash-of-white.
// It is a readable (non-HttpOnly) cookie because the cross-origin FastAPI backend
// is authorized via the `Authorization: Bearer` header (read here), not cookies.
const TOKEN_KEY = 'lode_token';

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
// Status normalization
// ---------------------------------------------------------------------------

export function mapAnalysisStatus(status: string): AnalysisStatus {
  switch (status) {
    case 'completed':
      return 'completed';
    case 'failed':
      return 'failed';
    case 'running':
      return 'running';
    case 'pending':
      return 'pending';
    case 'canceled':
      // canceled == needs human-in-the-loop attention
      return 'needs_human';
    default:
      throw new Error(`Unknown analysis status: ${status}`);
  }
}

export function mapStepStatus(status: string): StepStatus {
  switch (status) {
    case 'completed':
      return 'done';
    case 'running':
      return 'running';
    default:
      throw new Error(`Unknown step status: ${status}`);
  }
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function getJson<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: 'no-store',
      headers: authHeaders(),
    });
  } catch {
    // A rejected promise here is a network-level failure (backend down, CORS
    // block, offline) — the browser reports it as the unhelpful
    // "TypeError: Failed to fetch". Surface a clear, actionable message so the
    // UI can show what actually went wrong instead of the raw TypeError.
    throw new Error(`network error: ${API_BASE}${path}`);
  }
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    throw new Error(`request failed: ${res.status} ${path}`);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Shapes returned by the API (snake_case)
// ---------------------------------------------------------------------------

interface ApiAnalysis {
  dedupe_key: string;
  application_id: number;
  application_name: string;
  title: string;
  level: string;
  status: string;
  confidence: number | null;
  conclusion: string | null;
  received_at: string | null;
  updated_at: string;
}

interface ApiStep {
  node_type: string;
  status: string;
  order_index: number;
  detail: string | null;
  summary: string | null;
}

interface ApiHint {
  id: number;
  author: string;
  content: string;
  created_at: string;
}

interface ApiAlert {
  title: string;
  level: string;
  env: string;
  topic: string;
  error_message: string;
  fields: Record<string, unknown>;
}

export interface AnalysisDetail {
  dedupe_key: string;
  application_id: number;
  application_name: string;
  status: string;
  confidence: number | null;
  conclusion: string | null;
  evidence: Record<string, unknown> | null;
  alert: ApiAlert | null;
  steps: ApiStep[];
  hints: ApiHint[];
  matched_memory: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

interface ApiApplication {
  id: number;
  name: string;
  topic: string | null;
  latest_level: string;
  repo_count: number;
  created_at: string;
}

interface ApiMemory {
  id: number;
  application_id: number;
  application_name: string;
  trigger_signature: string;
  content: string;
  is_valid: boolean;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Public client functions
// ---------------------------------------------------------------------------

export async function fetchAnalyses(): Promise<Analysis[]> {
  const rows = await getJson<ApiAnalysis[]>('/analyses');
  return rows.map((r) => ({
    dedupeKey: r.dedupe_key,
    applicationId: String(r.application_id),
    applicationName: r.application_name,
    title: r.title,
    level: r.level as Level,
    status: mapAnalysisStatus(r.status),
    confidence: r.confidence,
    conclusion: r.conclusion,
  }));
}

export async function fetchAnalysis(dedupeKey: string): Promise<AnalysisDetail> {
  return getJson<AnalysisDetail>(
    `/analyses/${encodeURIComponent(dedupeKey)}`
  );
}

export async function reanalyze(dedupeKey: string): Promise<void> {
  const res = await fetch(`${API_BASE}/analyses/${encodeURIComponent(dedupeKey)}/reanalyze`, {
    method: 'POST',
    cache: 'no-store',
    headers: authHeaders(),
  });
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? `reanalyze failed: ${res.status}`);
  }
}

export async function addHint(
  dedupeKey: string,
  content: string
): Promise<void> {
  const res = await fetch(`${API_BASE}/analyses/${encodeURIComponent(dedupeKey)}/hints`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ content, author: 'web' }),
  });
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? `add hint failed: ${res.status}`);
  }
}

export interface LoginResult {
  token: string;
  user: CurrentUser;
}

export async function login(email: string, password: string): Promise<LoginResult> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? 'login failed');
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
    createdAt: r.created_at,
  }));
}

export async function fetchApplication(id: string): Promise<{
  id: number;
  name: string;
  topic: string | null;
  repos: { id: number; repo_id: number; name: string; url: string; description: string }[];
  preset_prompts: { id: number; type: string; content: string }[];
  db_sources: { id: number; name: string; conn_secret_ref: string; allowed_tables: unknown }[];
}> {
  return getJson(`/applications/${id}`);
}

// ---------------------------------------------------------------------------
// Per-application admin writes
// ---------------------------------------------------------------------------
//
// These back the per-application Settings tabs (Kafka topic, repos, prompts,
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
  description: string;
}

export async function bindRepo(
  applicationId: string | number,
  input: BindRepoInput
): Promise<ApplicationRepoRow> {
  return postJson(`/applications/${applicationId}/repos`, input);
}

export async function unbindRepo(
  applicationId: string | number,
  repoId: number
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/applications/${applicationId}/repos/${repoId}`,
    {
      method: 'DELETE',
      headers: authHeaders(),
    }
  );
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const b = await res.json().catch(() => null);
    throw new Error(b?.error?.message ?? `unbind repo failed: ${res.status}`);
  }
}

export interface CreateDbSourceInput {
  name: string;
  conn_secret_ref: string;
  allowed_tables: string[];
}

export interface DbSourceRow {
  id: number;
  application_id: number;
  name: string;
  conn_secret_ref: string;
  allowed_tables: string[];
}

export async function createDbSource(
  applicationId: string | number,
  input: CreateDbSourceInput
): Promise<DbSourceRow> {
  return postJson(`/applications/${applicationId}/db-sources`, input);
}

export async function deleteDbSource(
  applicationId: string | number,
  sourceId: number
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/applications/${applicationId}/db-sources/${sourceId}`,
    { method: 'DELETE', headers: authHeaders() }
  );
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const b = await res.json().catch(() => null);
    throw new Error(b?.error?.message ?? `delete data source failed: ${res.status}`);
  }
}

export interface CreatePresetPromptInput {
  type: 'deploy' | 'other';
  content: string;
}

export interface PresetPromptRow {
  id: number;
  application_id: number;
  type: string;
  content: string;
}

export async function createPresetPrompt(
  applicationId: string | number,
  input: CreatePresetPromptInput
): Promise<PresetPromptRow> {
  return postJson(`/applications/${applicationId}/prompts`, input);
}

export async function deletePresetPrompt(
  applicationId: string | number,
  promptId: number
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/applications/${applicationId}/prompts/${promptId}`,
    { method: 'DELETE', headers: authHeaders() }
  );
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const b = await res.json().catch(() => null);
    throw new Error(b?.error?.message ?? `delete prompt failed: ${res.status}`);
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
    createdAt: row.created_at,
  };
}

export async function fetchMemories(applicationId?: number): Promise<Memory[]> {
  const qs = applicationId != null ? `?application_id=${applicationId}` : '';
  const rows = await getJson<ApiMemory[]>(`/memories${qs}`);
  return rows.map((r) => ({
    id: String(r.id),
    applicationName: r.application_name,
    triggerSignature: r.trigger_signature,
    content: r.content,
    valid: r.is_valid,
  }));
}

export function toUiSteps(steps: ApiStep[]): AnalysisStep[] {
  return steps.map((s) => ({
    nodeType: s.node_type as AnalysisStep['nodeType'],
    title: s.summary ?? s.node_type,
    status: mapStepStatus(s.status),
    detail: s.detail ?? undefined,
  }));
}

export interface GlobalSettings {
  git_credentials: { id: number; auth_type: string; username: string; readonly: boolean; note: string; has_secret: boolean }[];
  git_repos: { id: number; name: string; repo_url: string; default_branch: string }[];
  ai_model_configs: {
    id: number;
    scope: string;
    application_id: number | null;
    provider: string;
    base_url: string;
    model: string;
    is_default: boolean;
    has_key: boolean;
  }[];
}

export async function fetchSettings(): Promise<GlobalSettings> {
  return getJson<GlobalSettings>('/settings');
}

export interface AiModelInput {
  scope: string;
  application_id: number | null;
  provider: string;
  base_url: string;
  api_key_ref: string;
  model: string;
  is_default: boolean;
}

export async function createAiModel(input: AiModelInput): Promise<AiModelConfig> {
  return postJson<AiModelConfig>('/settings/ai-models', input);
}

export async function fetchAiModelConfigs(): Promise<AiModelConfig[]> {
  return getJson<AiModelConfig[]>('/settings/ai-models');
}

export async function updateAiModel(id: number, input: AiModelInput): Promise<AiModelConfig> {
  return putJson<AiModelConfig>(`/settings/ai-models/${id}`, input);
}

export async function deleteAiModel(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/settings/ai-models/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) throw new Error(`delete ai model failed: ${res.status}`);
}

// ---------------------------------------------------------------------------
// Current user / account
// ---------------------------------------------------------------------------

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return getJson<CurrentUser>('/auth/me');
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/change-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? `change password failed: ${res.status}`);
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
  const res = await fetch(`${API_BASE}/users/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? `delete user failed: ${res.status}`);
  }
}

export async function resetUserPassword(id: number, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/users/${id}/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ password }),
  });
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? `reset password failed: ${res.status}`);
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
  const res = await fetch(`${API_BASE}/invites/accept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, password, name }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? `accept invite failed: ${res.status}`);
  }
}

// ---------------------------------------------------------------------------
// Private JSON helpers (POST/PUT with auth)
// ---------------------------------------------------------------------------

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const b = await res.json().catch(() => null);
    throw new Error(b?.error?.message ?? `request failed: ${res.status} ${path}`);
  }
  return (await res.json()) as T;
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    clearToken();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const b = await res.json().catch(() => null);
    throw new Error(b?.error?.message ?? `request failed: ${res.status} ${path}`);
  }
  return (await res.json()) as T;
}
