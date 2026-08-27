import type {
  BuildUnit,
  Component,
  CurrentUser,
  EvidenceConnector,
  InvestigationDetail,
  InvestigationAuditKind,
  InvestigationAuditPage,
  InvestigationListPage,
  InvestigationOverview,
  InvestigationPolicy,
  InvestigationSummary,
  ModelBinding,
  ProviderAccountModel,
  PlatformSettings,
  ProviderAccount,
  RepositoryBinding,
  GitAccountConnection,
  GitAccountRepository,
  GitProviderInstance,
  WorkspaceGitAccountGrant,
  WorkspaceRepositoryCandidate,
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

export function login(username: string, password: string) {
  return send<{ token: string; user: CurrentUser }>('/auth/login', 'POST', { username, password });
}
export function fetchCurrentUser() { return get<CurrentUser>('/auth/me'); }
export function fetchUsers() { return get<CurrentUser[]>('/users'); }
export function createUser(input: { username: string; display_name: string; initial_password: string }) {
  return send<CurrentUser>('/users', 'POST', input);
}
export function updateUser(id: number, input: { display_name?: string; status?: string }) {
  return send<CurrentUser>(`/users/${id}`, 'PATCH', input);
}
export function resetUserPassword(id: number, password: string) {
  return send(`/users/${id}/reset-password`, 'POST', { password });
}
export function changePassword(current_password: string, new_password: string) {
  return send('/auth/change-password', 'POST', { current_password, new_password });
}

export function fetchWorkspaces() { return get<Workspace[]>('/workbench/workspaces'); }
export function fetchWorkspace(id: number | string) { return get<Workspace>(`/workspaces/${id}`); }
export function fetchWorkspaceMembers(id: number | string) { return get<import('./types').WorkspaceMember[]>(`/workspaces/${id}/members`); }
export function putWorkspaceMember(workspaceId: number | string, userId: number, permission: 'viewer' | 'operator') {
  return send<import('./types').WorkspaceMember>(`/workspaces/${workspaceId}/members/${userId}`, 'PUT', { permission });
}
export function removeWorkspaceMember(workspaceId: number | string, userId: number) {
  return send<void>(`/workspaces/${workspaceId}/members/${userId}`, 'DELETE');
}
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
export function fetchPlatformSettings() { return get<PlatformSettings>('/platform-settings'); }
export function updatePlatformSettings(input: { ai_output_language: 'en' | 'zh'; expected_revision: number }) {
  return send<PlatformSettings>('/platform-settings', 'PUT', input);
}
export function fetchInvestigationPolicy(workspaceId: number | string) {
  return get<InvestigationPolicy>(`/workspaces/${workspaceId}/investigation-policy`);
}
export function updateInvestigationPolicy(workspaceId: number | string, profile: InvestigationPolicy['profile']) {
  return send<InvestigationPolicy>(`/workspaces/${workspaceId}/investigation-policy`, 'PUT', { profile });
}

export function fetchProviderAccounts() { return get<ProviderAccount[]>('/ai-provider-accounts'); }
export function discoverProviderModels(input: { base_url: string; credential: string; organization_ref?: string; project_ref?: string }) {
  return send<Array<{ provider_model_id: string; display_name: string }>>('/ai-provider-accounts/discover-models', 'POST', input);
}
export function discoverSavedProviderModels(id: number) {
  return send<Array<{ provider_model_id: string; display_name: string }>>(`/ai-provider-accounts/${id}/discover-models`, 'POST');
}
export function createProviderAccount(input: Record<string, unknown>) {
  return send<ProviderAccount>('/ai-provider-accounts', 'POST', input);
}
export function updateProviderAccount(id: number, input: Record<string, unknown>) {
  return send<ProviderAccount>(`/ai-provider-accounts/${id}`, 'PATCH', input);
}
export function updateProviderAccountModels(id: number, input: { model_ids: string[]; manual_model_ids: string[] }) {
  return send<ProviderAccount>(`/ai-provider-accounts/${id}/models`, 'PUT', input);
}
export function testProviderAccountModel(accountId: number, accountModelId: number) {
  return send<Record<string, unknown>>(`/ai-provider-accounts/${accountId}/models/${accountModelId}/test`, 'POST');
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
export function fetchGitProviderInstances() { return get<GitProviderInstance[]>('/git-provider-instances'); }
export function createGitProviderInstance(input: Record<string, unknown>) {
  return send<GitProviderInstance>('/git-provider-instances', 'POST', input);
}
export function fetchGitAccountConnections() { return get<GitAccountConnection[]>('/git-account-connections'); }
export function createGitAccountAccessToken(input: { provider_instance_id: number; name: string; access_token: string }) {
  return send<GitAccountConnection>('/git-account-connections/access-token', 'POST', input);
}
export function createGitHubAppConnection(input: { provider_instance_id: number; name: string; installation_id: string }) {
  return send<GitAccountConnection>('/git-account-connections/github-app', 'POST', input);
}
export function syncGitAccountConnection(id: number) {
  return send<GitAccountConnection>(`/git-account-connections/${id}/sync`, 'POST');
}
export function fetchGitAccountRepositories(id: number) {
  return get<GitAccountRepository[]>(`/git-account-connections/${id}/repositories`);
}
export function startGitOAuth(providerId: number, name: string) {
  return send<{ authorization_url: string }>(`/git-provider-instances/${providerId}/oauth/start`, 'POST', { name });
}
export function fetchWorkspaceGitAccountGrants(workspaceId: number | string) {
  return get<WorkspaceGitAccountGrant[]>(`/workspaces/${workspaceId}/git-account-grants`);
}
export function createWorkspaceGitAccountGrant(workspaceId: number | string, input: { account_connection_id: number; repository_scope: 'selected' | 'all_visible'; repository_ids?: number[] }) {
  return send<WorkspaceGitAccountGrant>(`/workspaces/${workspaceId}/git-account-grants`, 'POST', input);
}
export function fetchWorkspaceRepositoryCandidates(workspaceId: number | string) {
  return get<WorkspaceRepositoryCandidate[]>(`/workspaces/${workspaceId}/repository-candidates`);
}
export function fetchBuildUnits(workspaceId: number | string) {
  return get<{ items: BuildUnit[] }>(`/workspaces/${workspaceId}/build-units`);
}
export function fetchComponents(workspaceId: number | string) {
  return get<{ items: Component[] }>(`/workspaces/${workspaceId}/components`);
}
export function bindRepository(workspaceId: number | string, input: { repository_entitlement_id: number; role: string; priority?: number; description?: string }) {
  return send<RepositoryBinding>(`/workspaces/${workspaceId}/repositories`, 'POST', input);
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
export function fetchInvestigations(input: { workspaceId?: number; status?: string; q?: string; afterId?: number } = {}) {
  const query = new URLSearchParams();
  if (input.workspaceId) query.set('workspace_id', String(input.workspaceId));
  if (input.status && input.status !== 'all') query.set('status', input.status);
  if (input.q?.trim()) query.set('q', input.q.trim());
  if (input.afterId) query.set('after_id', String(input.afterId));
  const suffix = query.size ? `?${query.toString()}` : '';
  return get<InvestigationListPage>(`/investigations${suffix}`);
}
export function createInvestigation(input: Record<string, unknown>) {
  return send<{ id: string; workspace_id: number; status: string; job_id: number }>('/investigations', 'POST', input);
}
export function fetchInvestigation(id: string) { return get<InvestigationOverview>(`/investigations/${encodeURIComponent(id)}`); }
export function fetchInvestigationTechnical(id: string) { return get<InvestigationDetail>(`/investigations/${encodeURIComponent(id)}/technical`); }
export function fetchInvestigationAudit(id: string, kind: InvestigationAuditKind, afterId = 0) {
  return get<InvestigationAuditPage>(`/investigations/${encodeURIComponent(id)}/audit?kind=${kind}&after_id=${afterId}`);
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
