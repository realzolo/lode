import type {
  BuildUnit,
  Component,
  CurrentUser,
  ConnectorCreateInput,
  EvidenceConnector,
  InvestigationExecutionArtifactPage,
  InvestigationExecutionGraph,
  InvestigationExecutionNodeDetail,
  InvestigationListPage,
  InvestigationOverview,
  InvestigationSummary,
  ModelBinding,
  ModelBindingCreateInput,
  ProviderAccountModel,
  ProviderModelCatalogItem,
  ProviderModelDiscovery,
  PlatformSettings,
  ProviderAccount,
  RepositoryBinding,
  GitAccount,
  GitAccountRepository,
  Workspace,
  WorkspaceArchitectureContext,
  WorkspaceReadiness,
  RepositoryAnalysisJob,
} from '@/lib/types';

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';
const TOKEN_KEY = 'lode_token';
export const SESSION_EXPIRED_EVENT = 'lode:session-expired';

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    public readonly status: number,
    public readonly serverMessage: string | null = null,
    public readonly details: Record<string, unknown> | null = null,
  ) {
    super(serverMessage || code);
    this.name = 'ApiError';
  }
}

export function apiErrorMessage(cause: unknown, fallback: string): string {
  return cause instanceof ApiError && cause.serverMessage ? cause.serverMessage : fallback;
}

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
    throw new ApiError('network_error', 0);
  }
  if (response.status === 401) {
    clearToken();
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      error?: { code?: string; message?: string; details?: Record<string, unknown> };
    } | null;
    throw new ApiError(
      body?.error?.code || 'request_failed',
      response.status,
      body?.error?.message || null,
      body?.error?.details || null,
    );
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
export function createWorkspace(input: { name: string; description?: string; ingestion_topic: string }) {
  return send<Workspace>('/workspaces', 'POST', input);
}
export function updateWorkspace(id: number | string, input: { name?: string; description?: string; ingestion_topic?: string }) {
  return send<Workspace>(`/workspaces/${id}`, 'PATCH', input);
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
export function fetchWorkspaceReadiness(id: number | string) {
  return get<WorkspaceReadiness>(`/workspaces/${id}/readiness`);
}
export function fetchWorkspaceArchitectureContext(id: number | string) {
  return get<WorkspaceArchitectureContext>(`/workspaces/${id}/architecture-context`);
}
export function updateWorkspaceArchitectureContext(id: number | string, entries: WorkspaceArchitectureContext['entries']) {
  return send<WorkspaceArchitectureContext>(`/workspaces/${id}/architecture-context`, 'PUT', { entries });
}
export function fetchPlatformSettings() { return get<PlatformSettings>('/platform-settings'); }
export function updatePlatformSettings(input: { ai_output_language: 'en' | 'zh'; expected_revision: number }) {
  return send<PlatformSettings>('/platform-settings', 'PUT', input);
}
export function fetchProviderAccounts() { return get<ProviderAccount[]>('/ai-provider-accounts'); }
export function createProviderAccount(input: Record<string, unknown>) {
  return send<ProviderAccount>('/ai-provider-accounts', 'POST', input);
}
export function updateProviderAccount(id: number, input: Record<string, unknown>) {
  return send<ProviderAccount>(`/ai-provider-accounts/${id}`, 'PATCH', input);
}
export function updateProviderAccountModels(id: number, input: { models: Array<{ provider_model_id: string; source: 'discovered' | 'manual' }> }) {
  return send<ProviderAccount>(`/ai-provider-accounts/${id}/models`, 'PUT', input);
}
export function fetchProviderModelCatalog(providerKind: 'openai' | 'anthropic', protocolId: string) {
  const query = new URLSearchParams({ provider_kind: providerKind, protocol_id: protocolId });
  return get<ProviderModelCatalogItem[]>(`/ai-provider-model-catalog?${query}`);
}
export function discoverProviderModels(input: { provider_kind: 'openai' | 'anthropic'; protocol_id: string; base_url: string; api_key: string }) {
  return send<ProviderModelDiscovery>('/ai-provider-accounts/discover-models', 'POST', input);
}
export function refreshProviderModels(id: number) {
  return send<ProviderModelDiscovery>(`/ai-provider-accounts/${id}/discover-models`, 'POST');
}
export function testProviderAccountModel(accountId: number, accountModelId: number) {
  return send<Record<string, unknown>>(`/ai-provider-accounts/${accountId}/models/${accountModelId}/test`, 'POST');
}

export function fetchModelBindings(workspaceId: number | string) {
  return get<ModelBinding[]>(`/workspaces/${workspaceId}/model-bindings`);
}
export function createModelBinding(workspaceId: number | string, input: ModelBindingCreateInput) {
  return send<ModelBinding>(`/workspaces/${workspaceId}/model-bindings`, 'POST', input);
}
export function publishModelPolicy(workspaceId: number | string, input: Record<string, unknown>) {
  return send<Record<string, unknown>>(`/workspaces/${workspaceId}/model-policy`, 'PUT', input);
}

export function fetchRepositories(workspaceId: number | string) {
  return get<RepositoryBinding[]>(`/workspaces/${workspaceId}/repositories`);
}
export function fetchGitAdapters() { return get<import('./types').GitAdapter[]>('/git-adapters'); }
export function fetchGitAccounts() { return get<GitAccount[]>('/git-accounts'); }
export function createGitAccount(input: { adapter_id: string; name: string; api_url?: string; access_token: string }) {
  return send<GitAccount>('/git-accounts', 'POST', input);
}
export function syncGitAccount(id: number) { return send<GitAccount>(`/git-accounts/${id}/sync`, 'POST'); }
export function fetchGitAccountRepositories(id: number) {
  return get<GitAccountRepository[]>(`/git-accounts/${id}/repositories`);
}
export function fetchGitAccountRepositoryBranches(
  accountId: number,
  repositoryId: number,
  input: { cursor?: string; q?: string } = {},
) {
  const query = new URLSearchParams();
  if (input.cursor) query.set('cursor', input.cursor);
  if (input.q?.trim()) query.set('q', input.q.trim());
  const suffix = query.size ? `?${query.toString()}` : '';
  return get<import('./types').GitBranchPage>(`/git-accounts/${accountId}/repositories/${repositoryId}/branches${suffix}`);
}
export function fetchBuildUnits(workspaceId: number | string) {
  return get<{ items: BuildUnit[] }>(`/workspaces/${workspaceId}/build-units`);
}
export function fetchComponents(workspaceId: number | string) {
  return get<{ items: Component[] }>(`/workspaces/${workspaceId}/components`);
}
export function fetchRepositoryAnalysis(workspaceId: number | string) {
  return get<RepositoryAnalysisJob | null>(`/workspaces/${workspaceId}/repository-analysis`);
}
export function startRepositoryAnalysis(workspaceId: number | string) {
  return send<RepositoryAnalysisJob>(`/workspaces/${workspaceId}/repository-analysis`, 'POST');
}
export function fetchRepositoryAnalysisIssues(workspaceId: number | string, jobId: number, after?: number) {
  const suffix = after === undefined ? '' : `?after=${after}`;
  return get<import('./types').RepositoryAnalysisIssuePage>(`/workspaces/${workspaceId}/repository-analysis/${jobId}/issues${suffix}`);
}
export function bindRepository(workspaceId: number | string, input: { account_connection_id: number; repository_id: number; analysis_mode: 'code' | 'documentation'; is_alert_source: boolean; branch_mode?: 'default' | 'branch'; branch_name?: string | null; priority?: number; description?: string }) {
  return send<RepositoryBinding>(`/workspaces/${workspaceId}/repositories`, 'POST', input);
}
export function updateRepositoryBinding(workspaceId: number | string, bindingId: number, input: { expected_revision: number; analysis_mode?: 'code' | 'documentation'; is_alert_source?: boolean; branch_mode?: 'default' | 'branch'; branch_name?: string | null; priority?: number; description?: string; state?: 'active' | 'disabled' }) {
  return send<RepositoryBinding>(`/workspaces/${workspaceId}/repositories/${bindingId}`, 'PATCH', input);
}
export function disableRepositoryBinding(workspaceId: number | string, bindingId: number, expectedRevision: number) {
  return send<void>(`/workspaces/${workspaceId}/repositories/${bindingId}?expected_revision=${expectedRevision}`, 'DELETE');
}
export function fetchConnectorKinds() {
  return get<Array<{ kind: string; language: string; capabilities: string[]; secret_fields: string[] }>>('/evidence-connector-kinds');
}
export function fetchConnectors(workspaceId: number | string) {
  return get<EvidenceConnector[]>(`/workspaces/${workspaceId}/evidence-connectors`);
}
export function createConnector(workspaceId: number | string, input: ConnectorCreateInput) {
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
  return send<{ id: number; workspace_id: number; status: string; job_id: number }>('/investigations', 'POST', input);
}
export function fetchInvestigation(id: number | string) { return get<InvestigationOverview>(`/investigations/${encodeURIComponent(id)}`); }
export function fetchInvestigationExecutionGraph(id: number | string) {
  return get<InvestigationExecutionGraph>(`/investigations/${encodeURIComponent(id)}/execution-graph`);
}
export function fetchInvestigationExecutionNode(id: number | string, nodeId: string) {
  return get<InvestigationExecutionNodeDetail>(`/investigations/${encodeURIComponent(id)}/execution-graph/nodes/${encodeURIComponent(nodeId)}`);
}
export function fetchInvestigationExecutionArtifact(
  id: number | string,
  nodeId: string,
  artifactId: number,
  afterIndex = 0,
) {
  const query = new URLSearchParams({ after_index: String(afterIndex), limit: '100' });
  return get<InvestigationExecutionArtifactPage>(`/investigations/${encodeURIComponent(id)}/execution-graph/nodes/${encodeURIComponent(nodeId)}/artifacts/${encodeURIComponent(artifactId)}?${query.toString()}`);
}
export function retryInvestigation(id: number | string) {
  return send<{ id: number }>(`/investigations/${encodeURIComponent(id)}/retry`, 'POST');
}
export function archiveInvestigation(id: number | string) {
  return send(`/investigations/${encodeURIComponent(id)}/archive`, 'POST');
}

export function openInvestigationStream(
  id: number | string,
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
