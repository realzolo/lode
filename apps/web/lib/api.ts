import type {
  CurrentUser,
  EvidenceConnector,
  InvestigationDetail,
  InvestigationSummary,
  Invite,
  ModelBinding,
  ModelDeployment,
  ProviderAccount,
  RepositoryBinding,
  Workspace,
} from '@/lib/types';

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';
const TOKEN_KEY = 'lode_token';
export const SESSION_EXPIRED_EVENT = 'lode:session-expired';

export function getToken(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${TOKEN_KEY}=([^;]*)`));
  return match ? decodeURIComponent(match[2]) : null;
}

export function setToken(token: string): void {
  if (typeof document === 'undefined') return;
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `${TOKEN_KEY}=${encodeURIComponent(token)}; Path=/; Max-Age=${tokenMaxAge(token)}; SameSite=Lax${secure}`;
}

export function clearToken(): void {
  if (typeof document === 'undefined') return;
  document.cookie = `${TOKEN_KEY}=; Path=/; Max-Age=0; SameSite=Lax`;
}

function tokenMaxAge(token: string): number {
  try {
    const segment = token.split('.')[1] ?? '';
    const payload = JSON.parse(atob(segment.replace(/-/g, '+').replace(/_/g, '/')));
    return typeof payload.exp === 'number'
      ? Math.max(0, payload.exp - Math.floor(Date.now() / 1000))
      : 604800;
  } catch {
    return 604800;
  }
}

function authHeaders(json = false): HeadersInit {
  const token = getToken();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(json ? { 'Content-Type': 'application/json' } : {}),
  };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { cache: 'no-store', ...init });
  } catch {
    throw new Error(`Network error: ${API_BASE}${path}`);
  }
  if (response.status === 401) {
    clearToken();
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      error?: { message?: string; details?: unknown };
    } | null;
    throw new Error(body?.error?.message || `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function get<T>(path: string): Promise<T> {
  return request<T>(path, { headers: authHeaders() });
}

function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: authHeaders(body !== undefined),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
}

export function login(email: string, password: string) {
  return send<{ token: string; user: CurrentUser }>('/auth/login', 'POST', { email, password });
}
export function fetchCurrentUser() { return get<CurrentUser>('/auth/me'); }
export function fetchUsers() { return get<CurrentUser[]>('/users'); }
export function createUser(input: { email: string; name: string; role: string; password: string }) {
  return send<CurrentUser>('/users', 'POST', input);
}
export function updateUser(id: number, input: { name?: string; role?: string; status?: string }) {
  return send<CurrentUser>(`/users/${id}`, 'PUT', input);
}
export function deleteUser(id: number) { return send<void>(`/users/${id}`, 'DELETE'); }
export function resetUserPassword(id: number, password: string) {
  return send(`/users/${id}/reset-password`, 'POST', { password });
}
export function fetchInvites() { return get<Invite[]>('/invites'); }
export function createInvite(email: string) { return send<Invite>('/invites', 'POST', { email }); }
export function acceptInvite(token: string, password: string, name: string) {
  return send('/invites/accept', 'POST', { token, password, name });
}

export function fetchWorkspaces() { return get<Workspace[]>('/workspaces'); }
export function fetchWorkspace(id: number | string) { return get<Workspace>(`/workspaces/${id}`); }
export function createWorkspace(input: { name: string; ingestion_topic: string }) {
  return send<Workspace>('/workspaces', 'POST', input);
}
export function startIngestion(id: number, startPosition: 'earliest' | 'latest') {
  return send<Workspace>(`/workspaces/${id}/ingestion/start`, 'POST', { start_position: startPosition });
}
export function pauseIngestion(id: number) {
  return send<Workspace>(`/workspaces/${id}/ingestion/pause`, 'POST');
}
export function resumeIngestion(id: number) {
  return send<Workspace>(`/workspaces/${id}/ingestion/resume`, 'POST');
}
export function fetchCapabilities(id: number | string) {
  return get<{ models: number; repositories: number; healthy_connectors: number; gaps: string[] }>(`/workspaces/${id}/capabilities`);
}

export function fetchProviderAccounts() { return get<ProviderAccount[]>('/ai-provider-accounts'); }
export function createProviderAccount(input: Record<string, unknown>) {
  return send<ProviderAccount>('/ai-provider-accounts', 'POST', input);
}
export function testProviderAccount(id: number) {
  return send<Record<string, unknown>>(`/ai-provider-accounts/${id}/test`, 'POST');
}
export function introspectProviderModels(id: number) {
  return send<{ models: Array<{ id: string }> }>(`/ai-provider-accounts/${id}/introspect-models`, 'POST');
}
export function fetchModelDeployments() { return get<ModelDeployment[]>('/ai-model-deployments'); }
export function createModelDeployment(providerId: number, input: Record<string, unknown>) {
  return send<ModelDeployment>(`/ai-provider-accounts/${providerId}/model-deployments`, 'POST', input);
}
export function testModelDeployment(id: number) {
  return send<Record<string, unknown>>(`/ai-model-deployments/${id}/test`, 'POST');
}

export function fetchModelBindings(workspaceId: number | string) {
  return get<ModelBinding[]>(`/workspaces/${workspaceId}/model-bindings`);
}
export function createModelBinding(workspaceId: number | string, input: Record<string, unknown>) {
  return send<ModelBinding>(`/workspaces/${workspaceId}/model-bindings`, 'POST', input);
}
export function publishModelPolicy(workspaceId: number | string, input: Record<string, unknown>) {
  return send<Record<string, unknown>>(`/workspaces/${workspaceId}/model-policy`, 'PUT', input);
}

export function fetchRepositories(workspaceId: number | string) {
  return get<RepositoryBinding[]>(`/workspaces/${workspaceId}/repositories`);
}
export function createLocalRepository(workspaceId: number | string, input: Record<string, unknown>) {
  return send<RepositoryBinding>(`/workspaces/${workspaceId}/repositories/local`, 'POST', input);
}
export function fetchConnectorKinds() {
  return get<Array<{ kind: string; language: string; capabilities: string[]; secret_fields: string[] }>>('/evidence-connector-kinds');
}
export function fetchConnectors(workspaceId: number | string) {
  return get<EvidenceConnector[]>(`/workspaces/${workspaceId}/evidence-connectors`);
}
export function createConnector(workspaceId: number | string, input: Record<string, unknown>) {
  return send<EvidenceConnector>(`/workspaces/${workspaceId}/evidence-connectors`, 'POST', input);
}
export function testConnector(workspaceId: number | string, connectorId: number) {
  return send<Record<string, unknown>>(`/workspaces/${workspaceId}/evidence-connectors/${connectorId}/test`, 'POST');
}
export function introspectConnector(workspaceId: number | string, connectorId: number) {
  return send<Record<string, unknown>>(`/workspaces/${workspaceId}/evidence-connectors/${connectorId}/introspect`, 'POST');
}
export function fetchResourceView(workspaceId: number | string, resource: string) {
  return get<Array<Record<string, unknown>>>(`/workspaces/${workspaceId}/${resource}`);
}

export function fetchInvestigations(workspaceId?: number) {
  return get<InvestigationSummary[]>(`/investigations${workspaceId ? `?workspace_id=${workspaceId}` : ''}`);
}
export function createInvestigation(input: Record<string, unknown>) {
  return send<{ id: string; workspace_id: number; status: string; job_id: number }>('/investigations', 'POST', input);
}
export function fetchInvestigation(id: string) { return get<InvestigationDetail>(`/investigations/${encodeURIComponent(id)}`); }
export function fetchInvestigationAudit(id: string) {
  return get<Record<string, Array<Record<string, unknown>>>>(`/investigations/${encodeURIComponent(id)}/audit`);
}
export function retryInvestigation(id: string) {
  return send<{ id: string }>(`/investigations/${encodeURIComponent(id)}/retry`, 'POST');
}
export function archiveInvestigation(id: string) {
  return send(`/investigations/${encodeURIComponent(id)}/archive`, 'POST');
}

export function openInvestigationStream(
  id: string,
  after: number,
  onEvent: (event: Record<string, unknown>) => void,
): () => void {
  const controller = new AbortController();
  void (async () => {
    let cursor = after;
    while (!controller.signal.aborted) {
      let terminal = false;
      try {
        const response = await fetch(
          `${API_BASE}/investigations/${encodeURIComponent(id)}/stream?after=${cursor}`,
          { headers: authHeaders(), signal: controller.signal },
        );
        if (!response.ok || !response.body) throw new Error('Stream unavailable');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (!controller.signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split('\n\n');
          buffer = blocks.pop() ?? '';
          for (const block of blocks) {
            const idLine = block.split('\n').find((line) => line.startsWith('id: '));
            if (idLine) cursor = Math.max(cursor, Number(idLine.slice(4)) || 0);
            const data = block.split('\n').find((line) => line.startsWith('data: '));
            if (!data) continue;
            const event = JSON.parse(data.slice(6)) as Record<string, unknown>;
            terminal = event.status === 'completed' || event.status === 'failed';
            onEvent(event);
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) onEvent({ stream_error: String(error) });
      }
      if (terminal || controller.signal.aborted) break;
      await new Promise((resolve) => setTimeout(resolve, 1_000));
    }
  })();
  return () => controller.abort();
}
