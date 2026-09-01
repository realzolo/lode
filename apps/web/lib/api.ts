import type {
  BuildUnit,
  Component,
  CurrentUser,
  ConnectorCreateInput,
  EvidenceConnector,
  InvestigationExecutionArtifactPage,
  InvestigationExecutionGraph,
  InvestigationExecutionNodeDetail,
  InvestigationOverview,
  InvestigationReportView,
  IncidentListPage,
  IncidentOverview,
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
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const AUTH_REQUEST_TIMEOUT_MS = 10_000;

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

export function apiErrorMessage(_cause: unknown, fallback: string): string {
  // API error messages are implementation detail and are not localized. Each
  // caller supplies its route-localized recovery copy instead of leaking a
  // backend string into the dashboard language.
  return fallback;
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

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const abortForCaller = () => controller.abort();
  if (init.signal?.aborted) {
    controller.abort();
  } else {
    init.signal?.addEventListener('abort', abortForCaller, { once: true });
  }
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { cache: 'no-store', ...init, signal: controller.signal });
  } catch {
    if (controller.signal.aborted && !init.signal?.aborted) {
      throw new ApiError('request_timeout', 0);
    }
    throw new ApiError('network_error', 0);
  } finally {
    globalThis.clearTimeout(timeout);
    init.signal?.removeEventListener('abort', abortForCaller);
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

function get<T>(path: string, timeoutMs?: number): Promise<T> {
  return request<T>(path, { headers: authHeaders() }, timeoutMs);
}

function send<T>(path: string, method: string, body?: unknown, headers: HeadersInit = {}): Promise<T> {
  return request<T>(path, {
    method,
    headers: { ...authHeaders(body !== undefined), ...headers },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
}

export function login(username: string, password: string) {
  return send<{ token: string; user: CurrentUser }>('/auth/login', 'POST', { username, password });
}
export function fetchCurrentUser() { return get<CurrentUser>('/auth/me', AUTH_REQUEST_TIMEOUT_MS); }
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
export function fetchIncidents(input: {
  workspaceId?: number;
  state?: string;
  severity?: string;
  sourceType?: string;
  reportState?: string;
  assignedTo?: number;
  observedFrom?: string;
  observedTo?: string;
  q?: string;
  cursor?: string;
} = {}) {
  const query = new URLSearchParams();
  if (input.workspaceId) query.set('workspace_id', String(input.workspaceId));
  if (input.state && input.state !== 'all') query.set('state', input.state);
  if (input.severity && input.severity !== 'all') query.set('severity', input.severity);
  if (input.sourceType && input.sourceType !== 'all') query.set('source_type', input.sourceType);
  if (input.reportState && input.reportState !== 'all') query.set('report_state', input.reportState);
  if (input.assignedTo) query.set('assigned_to', String(input.assignedTo));
  if (input.observedFrom) query.set('observed_from', input.observedFrom);
  if (input.observedTo) query.set('observed_to', input.observedTo);
  if (input.q?.trim()) query.set('q', input.q.trim());
  if (input.cursor) query.set('cursor', input.cursor);
  const suffix = query.size ? `?${query.toString()}` : '';
  return get<IncidentListPage>(`/incidents${suffix}`);
}
export function createManualIncident(
  workspaceId: number,
  input: { schema_version: 'manual-incident.v1'; summary: string; error_text: string; trace_id?: string; repository_binding_id?: number },
) {
  return send<{ incident_id: number; signal_id: number; investigation_id: number | null; job_id: number | null }>(
    `/workspaces/${workspaceId}/manual-incidents`,
    'POST',
    input,
    { 'Idempotency-Key': crypto.randomUUID() },
  );
}
export function fetchIncident(id: number | string) { return get<IncidentOverview>(`/incidents/${encodeURIComponent(id)}`); }
export function fetchIncidentAssignees(id: number | string) { return get<import('./types').WorkspaceMember[]>(`/incidents/${encodeURIComponent(id)}/assignees`); }
export function transitionIncident(id: number | string, action: 'acknowledge' | 'mitigate' | 'resolve' | 'close' | 'reopen', input: { expected_state_version: number; reason: string }) {
  return send<IncidentOverview>(`/incidents/${encodeURIComponent(id)}/${action}`, 'POST', input);
}
export function assignIncident(id: number | string, input: { owner_id: number | null; expected_state_version: number; reason: string }) {
  return send<IncidentOverview>(`/incidents/${encodeURIComponent(id)}/assign`, 'POST', input);
}
export function startIncidentInvestigation(id: number | string) {
  return send<import('./types').InvestigationRun>(`/incidents/${encodeURIComponent(id)}/investigations`, 'POST');
}
export function retryIncidentInvestigation(incidentId: number | string, investigationId: number | string) {
  return send<import('./types').InvestigationRun>(`/incidents/${encodeURIComponent(incidentId)}/investigations/${encodeURIComponent(investigationId)}/retry`, 'POST');
}
export function createIncidentAction(incidentId: number | string, input: Record<string, unknown>) {
  return send<import('./types').IncidentAction>(`/incidents/${encodeURIComponent(incidentId)}/actions`, 'POST', input);
}
export function updateIncidentAction(incidentId: number | string, actionId: number | string, input: Record<string, unknown>) {
  return send<import('./types').IncidentAction>(`/incidents/${encodeURIComponent(incidentId)}/actions/${encodeURIComponent(actionId)}`, 'PATCH', input);
}
export function classifyIncidentSeverity(id: number | string, input: { severity: 'WARNING' | 'CRITICAL'; expected_state_version: number; reason: string }) {
  return send<IncidentOverview>(`/incidents/${encodeURIComponent(id)}/severity`, 'POST', input);
}
export function fetchCorrelationCandidates(workspaceId: number, status = 'pending') {
  return get<import('./types').CorrelationCandidate[]>(`/workspaces/${workspaceId}/correlation-candidates?status=${status}`);
}
export function decideCorrelationCandidate(candidateId: number, decision: 'accept' | 'reject', reason: string) {
  return send<unknown>(`/incidents/correlation-candidates/${candidateId}/${decision}`, 'POST', { reason });
}
export function mergeIncident(targetId: number, source_incident_id: number, reason: string) {
  return send<IncidentOverview>(`/incidents/${targetId}/merge`, 'POST', { source_incident_id, reason });
}
export function splitIncident(sourceId: number, signal_ids: number[], title: string, reason: string) {
  return send<IncidentOverview>(`/incidents/${sourceId}/split`, 'POST', { signal_ids, title, reason });
}
export function fetchSimilarIncidents(incidentId: number) {
  return get<import('./types').SimilarIncident[]>(`/incidents/${incidentId}/similar-incidents`);
}
export function fetchActionProposals(incidentId: number) {
  return get<import('./types').ActionProposal[]>(`/incidents/${incidentId}/action-proposals`);
}
export function decideActionProposal(incidentId: number, proposalId: number, decision: 'accept' | 'reject', input: { reason: string; owner_id?: number }) {
  return send<import('./types').ActionProposal>(`/incidents/${incidentId}/action-proposals/${proposalId}/${decision}`, 'POST', input);
}
export function fetchInvestigation(id: number | string) { return get<InvestigationOverview>(`/investigations/${encodeURIComponent(id)}`); }
export function fetchInvestigationReport(id: number | string) { return get<InvestigationReportView>(`/investigations/${encodeURIComponent(id)}/report`); }
export function controlInvestigation(id: number, command: 'pause' | 'cancel' | 'resume', reason: string) {
  return send<Record<string, unknown>>(`/investigations/${id}/${command}`, 'POST', { reason });
}
export function addInvestigationEvidence(id: number, description: string, evidence_text: string) {
  return send<Record<string, unknown>>(`/investigations/${id}/evidence`, 'POST', { description, evidence_text });
}
export function askInvestigationQuestion(id: number, question: string) {
  return send<Record<string, unknown>>(`/investigations/${id}/questions`, 'POST', { question });
}
export function branchInvestigation(id: number, hypothesis: string) {
  return send<Record<string, unknown>>(`/investigations/${id}/branches`, 'POST', { hypothesis });
}
export function compareInvestigations(leftId: number, rightId: number) {
  return get<import('@/lib/types').InvestigationComparisonView>(`/investigations/comparisons?left_id=${leftId}&right_id=${rightId}`);
}
export function fetchInvestigationReviews(id: number) {
  return get<import('./types').InvestigationReview[]>(`/investigations/${id}/reviews`);
}
export function createInvestigationReview(id: number, input: { code_finding_id?: number; verdict: 'accepted' | 'rejected' | 'needs_evidence'; comment: string; supersedes_review_id?: number }) {
  return send<import('./types').InvestigationReview>(`/investigations/${id}/reviews`, 'POST', input);
}
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
