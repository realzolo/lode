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
  Application,
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
  user: { id: number; email: string; name: string; role: string; status: string };
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
    model: string;
    is_default: boolean;
    has_key: boolean;
  }[];
}

export async function fetchSettings(): Promise<GlobalSettings> {
  return getJson<GlobalSettings>('/settings');
}
