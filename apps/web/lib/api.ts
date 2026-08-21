// Thin client for the Incident Trace backend API.
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

const TOKEN_KEY = 'it_token';

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(TOKEN_KEY);
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
      return 'pending';
  }
}

export function mapStepStatus(status: string): StepStatus {
  switch (status) {
    case 'completed':
      return 'done';
    case 'running':
      return 'running';
    default:
      return 'pending';
  }
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: 'no-store',
    headers: authHeaders(),
  });
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
  }));
}

export async function fetchApplication(id: string): Promise<{
  id: number;
  name: string;
  topic: string | null;
  repos: { name: string; url: string; description: string }[];
  preset_prompts: { type: string; content: string }[];
  db_sources: { name: string; allowed_tables: unknown }[];
}> {
  return getJson(`/applications/${id}`);
}

export async function fetchMemories(): Promise<Memory[]> {
  const rows = await getJson<ApiMemory[]>('/memories');
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
