'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, CirclePause, CirclePlay, Database, GitBranch, Link2, Plus, RefreshCw, ScanSearch, ShieldCheck, Trash2, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Tabs } from '@/components/ui/tabs';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { EvidenceConnectorDialog as ConnectorDialog } from '@/components/evidence-connector-dialog';
import {
  bindRepository,
  apiErrorMessage,
  fetchBuildUnits,
  createModelBinding,
  createWorkspaceGitAccountGrant,
  fetchWorkspaceReadiness,
  fetchWorkspaceArchitectureContext,
  updateWorkspaceArchitectureContext,
  updateWorkspace,
  startIngestion,
  pauseIngestion,
  resumeIngestion,
  fetchComponents,
  fetchRepositoryAnalysis,
  startRepositoryAnalysis,
  fetchConnectorKinds,
  fetchConnectors,
  fetchGitAccounts,
  fetchGitAccountRepositories,
  fetchInvestigationPolicy,
  fetchModelBindings,
  fetchProviderAccounts,
  fetchRepositories,
  fetchWorkspace,
  fetchWorkspaceMembers,
  fetchUsers,
  fetchWorkspaceGitAccountGrants,
  fetchWorkspaceRepositoryCandidates,
  introspectConnector,
  publishModelPolicy,
  testConnector,
  updateInvestigationPolicy,
  putWorkspaceMember,
  removeWorkspaceMember,
} from '@/lib/api';
import { Link } from '@/lib/navigation';
import type {
  BuildUnit,
  Component,
  EvidenceConnector,
  GitAccount,
  GitAccountRepository,
  InvestigationPolicy,
  ModelBinding,
  ProviderAccount,
  ProviderAccountModel,
  RepositoryBinding,
  RepositoryAnalysisJob,
  Workspace,
  WorkspaceArchitectureContext,
  WorkspaceReadiness,
  ArchitectureContextKind,
  WorkspaceGitAccountGrant,
  WorkspaceRepositoryCandidate,
  WorkspaceMember,
  CurrentUser,
} from '@/lib/types';

const roles = ['planner', 'native_query', 'synthesizer', 'verifier', 'context_compactor'];
const repositoryRoleKeys = {
  runtime_source: 'runtimeSource',
  shared_library: 'sharedLibrary',
  infrastructure: 'infrastructure',
  documentation: 'documentation',
} as const;
const repositoryRoles = Object.keys(repositoryRoleKeys) as Array<keyof typeof repositoryRoleKeys>;
const modelRoleKeys = {
  planner: 'modelRoles.planner', native_query: 'modelRoles.native_query', synthesizer: 'modelRoles.synthesizer',
  verifier: 'modelRoles.verifier', context_compactor: 'modelRoles.context_compactor',
} as const;
const executionClassKeys = { latency_optimized: 'executionClasses.latency_optimized', reasoning_optimized: 'executionClasses.reasoning_optimized' } as const;
const readinessCheckKeys = { kafka_topic: 'readinessChecks.kafkaTopic', model_policy: 'readinessChecks.modelPolicy', repositories: 'readinessChecks.repositories', evidence_connectors: 'readinessChecks.evidenceConnectors', architecture_context: 'readinessChecks.architectureContext' } as const;
const identityStatusKeys = { verified: 'identityStatus.verified', provisional: 'identityStatus.provisional', ambiguous: 'identityStatus.ambiguous' } as const;
const componentKindKeys = { service: 'componentKinds.service', worker: 'componentKinds.worker', job: 'componentKinds.job', gateway: 'componentKinds.gateway', library_runtime: 'componentKinds.library_runtime', unknown: 'componentKinds.unknown' } as const;
const buildSystemKeys = { npm: 'buildSystems.npm', pnpm: 'buildSystems.pnpm', python: 'buildSystems.python', go: 'buildSystems.go', cargo: 'buildSystems.cargo', maven: 'buildSystems.maven', gradle: 'buildSystems.gradle', docker: 'buildSystems.docker', other: 'buildSystems.other' } as const;
const connectorCapabilityKeys = { bounded_log_query: 'connectorCapabilities.bounded_log_query', bounded_metric_query: 'connectorCapabilities.bounded_metric_query', schema_introspection: 'connectorCapabilities.schema_introspection', bounded_search: 'connectorCapabilities.bounded_search', bounded_select: 'connectorCapabilities.bounded_select', cost_explain: 'connectorCapabilities.cost_explain', bounded_https_read: 'connectorCapabilities.bounded_https_read' } as const;

function flattenAccountModels(accounts: ProviderAccount[]): ProviderAccountModel[] {
  return accounts.flatMap((account) => account.models.map((model) => ({ ...model, provider_account_id: account.id })));
}

export default function WorkspacePage({ params }: { params: { id: string } }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [bindings, setBindings] = useState<ModelBinding[]>([]);
  const [accountModels, setAccountModels] = useState<ProviderAccountModel[]>([]);
  const [repositories, setRepositories] = useState<RepositoryBinding[]>([]);
  const [buildUnits, setBuildUnits] = useState<BuildUnit[]>([]);
  const [components, setComponents] = useState<Component[]>([]);
  const [candidates, setCandidates] = useState<WorkspaceRepositoryCandidate[]>([]);
  const [grants, setGrants] = useState<WorkspaceGitAccountGrant[]>([]);
  const [connectors, setConnectors] = useState<EvidenceConnector[]>([]);
  const [investigationPolicy, setInvestigationPolicy] = useState<InvestigationPolicy | null>(null);
  const [kinds, setKinds] = useState<Array<{ kind: string; language: string; capabilities: string[]; secret_fields: string[] }>>([]);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [readiness, setReadiness] = useState<WorkspaceReadiness | null>(null);
  const [architectureContext, setArchitectureContext] = useState<WorkspaceArchitectureContext | null>(null);
  const [repositoryAnalysis, setRepositoryAnalysis] = useState<RepositoryAnalysisJob | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dialog, setDialog] = useState<'binding' | 'repository' | 'grant' | 'connector' | null>(null);

  const load = useCallback(async () => {
    try {
      const ws = await fetchWorkspace(params.id);
      setWorkspace(ws);
      const [modelResult, repositoryResult, connectorResult, overviewResult, memberResult] = await Promise.allSettled([
        Promise.all([fetchModelBindings(params.id), fetchProviderAccounts()]),
        Promise.all([
          fetchRepositories(params.id),
          fetchWorkspaceRepositoryCandidates(params.id),
          fetchWorkspaceGitAccountGrants(params.id),
          fetchBuildUnits(params.id),
          fetchComponents(params.id),
          fetchRepositoryAnalysis(params.id),
        ]),
        Promise.all([fetchConnectors(params.id), fetchConnectorKinds()]),
        Promise.all([
          fetchWorkspaceReadiness(params.id),
          fetchWorkspaceArchitectureContext(params.id),
          fetchInvestigationPolicy(params.id),
        ]),
        Promise.all([fetchWorkspaceMembers(params.id), fetchUsers()]),
      ]);

      if (modelResult.status === 'fulfilled') {
        setBindings(modelResult.value[0]);
        setAccountModels(flattenAccountModels(modelResult.value[1]));
      }
      if (repositoryResult.status === 'fulfilled') {
        const [repoRows, candidateRows, grantRows, buildUnitRows, componentRows, analysis] = repositoryResult.value;
        setRepositories(repoRows);
        setCandidates(candidateRows);
        setGrants(grantRows);
        setBuildUnits(buildUnitRows.items);
        setComponents(componentRows.items);
        setRepositoryAnalysis(analysis);
      }
      if (connectorResult.status === 'fulfilled') {
        setConnectors(connectorResult.value[0]);
        setKinds(connectorResult.value[1]);
      }
      if (overviewResult.status === 'fulfilled') {
        setReadiness(overviewResult.value[0]);
        setArchitectureContext(overviewResult.value[1]);
        setInvestigationPolicy(overviewResult.value[2]);
      }
      if (memberResult.status === 'fulfilled') {
        setMembers(memberResult.value[0]);
        setUsers(memberResult.value[1].filter((user) => !user.is_system_admin));
      }
      const failures = [modelResult, repositoryResult, connectorResult, overviewResult, memberResult]
        .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
        .map((result) => apiErrorMessage(result.reason, tc('requestFailed')));
      setError([...new Set(failures)].join(' '));
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally { setLoading(false); }
  }, [params.id, tc]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (workspace?.ingestion_state !== 'active' && !['queued', 'running'].includes(repositoryAnalysis?.state || '')) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load, repositoryAnalysis?.state, workspace?.ingestion_state]);

  const tabs = useMemo(() => [
    { value: 'overview', label: t('overview'), content: <Overview workspace={workspace} readiness={readiness} policy={investigationPolicy} architectureContext={architectureContext} onChanged={load} onWorkspaceChanged={setWorkspace} /> },
    { value: 'models', label: t('modelPolicy'), content: <Models workspaceId={params.id} bindings={bindings} accountModels={accountModels} onAdd={() => setDialog('binding')} onChanged={load} /> },
    { value: 'repositories', label: t('repositories'), content: <Repositories workspaceId={params.id} rows={repositories} grants={grants} buildUnits={buildUnits} components={components} analysis={repositoryAnalysis} onAuthorize={() => setDialog('grant')} onBind={() => setDialog('repository')} onChanged={load} /> },
    { value: 'connectors', label: t('connectors'), content: <Connectors workspaceId={params.id} rows={connectors} onAdd={() => setDialog('connector')} onChanged={load} /> },
    { value: 'members', label: t('members'), content: <Members workspaceId={params.id} members={members} users={users} onChanged={load} /> },
  ], [accountModels, architectureContext, bindings, buildUnits, components, connectors, grants, investigationPolicy, load, members, params.id, readiness, repositories, repositoryAnalysis, t, users, workspace]);

  if (loading) return <main className="dashboard-page space-y-6"><div className="h-16 border-b" /><ListSkeleton rows={7} columns={5} /></main>;

  return <main className="dashboard-page workspace-detail-page space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="mb-2 text-sm text-muted-foreground"><Link href="/admin" className="hover:text-link">{t('workspace')}</Link> / {params.id}</p>
        <h1 className="page-title">{workspace?.name || t('workspace')}</h1>
        <p className="page-subtitle mono">{workspace?.ingestion_topic}</p>
      </div>
      <Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => { setRefreshing(true); void load().finally(() => setRefreshing(false)); }}><RefreshCw size={16} /></Button>
    </header>
    {error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    <Tabs items={tabs} />
    <BindingDialog open={dialog === 'binding'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} accountModels={accountModels} onCreated={load} />
    <RepositoryDialog open={dialog === 'repository'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} candidates={candidates} onCreated={load} />
    <GrantDialog open={dialog === 'grant'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} onCreated={load} />
    <ConnectorDialog open={dialog === 'connector'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} kinds={kinds} onCreated={load} />
  </main>;
}

function Members({ workspaceId, members, users, onChanged }: { workspaceId: string; members: WorkspaceMember[]; users: CurrentUser[]; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [userId, setUserId] = useState('');
  const [permission, setPermission] = useState<'viewer' | 'operator'>('viewer');
  const [busy, setBusy] = useState<string | null>(null);
  async function save() { if (!userId) return; setBusy('grant'); try { await putWorkspaceMember(workspaceId, Number(userId), permission); await onChanged(); setUserId(''); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setBusy(null); } }
  async function update(memberId: number, next: 'viewer' | 'operator') { setBusy(`update-${memberId}`); try { await putWorkspaceMember(workspaceId, memberId, next); await onChanged(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setBusy(null); } }
  async function revoke(memberId: number) { setBusy(`revoke-${memberId}`); try { await removeWorkspaceMember(workspaceId, memberId); await onChanged(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setBusy(null); } }
  return <section className="space-y-4"><div className="flex flex-wrap gap-2"><Select value={userId} disabled={busy !== null} onChange={(event) => setUserId(event.target.value)}><option value="">{t('selectUser')}</option>{users.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}</Select><Select value={permission} disabled={busy !== null} onChange={(event) => setPermission(event.target.value as 'viewer' | 'operator')}><option value="viewer">{t('viewer')}</option><option value="operator">{t('operator')}</option></Select><Button loading={busy === 'grant'} loadingText={tc('saving')} disabled={!userId || busy !== null} onClick={() => void save()}>{t('grant')}</Button></div><div className="table-wrap"><table className="table"><thead><tr><th>{t('member')}</th><th>{t('permissions')}</th><th /></tr></thead><tbody>{members.map((member) => <tr key={member.user_id}><td>{member.username}</td><td><Select value={member.permission} disabled={busy !== null} onChange={(event) => void update(member.user_id, event.target.value as 'viewer' | 'operator')}><option value="viewer">{t('viewer')}</option><option value="operator">{t('operator')}</option></Select></td><td><Button size="sm" variant="outline" loading={busy === `revoke-${member.user_id}`} disabled={busy !== null} onClick={() => void revoke(member.user_id)}>{t('revoke')}</Button></td></tr>)}{!members.length ? <tr><td colSpan={3} className="py-8 text-center text-sm text-muted-foreground">{t('noMembers')}</td></tr> : null}</tbody></table></div></section>;
}

function Overview({ workspace, readiness, policy, architectureContext, onChanged, onWorkspaceChanged }: { workspace: Workspace | null; readiness: WorkspaceReadiness | null; policy: InvestigationPolicy | null; architectureContext: WorkspaceArchitectureContext | null; onChanged: () => Promise<void>; onWorkspaceChanged: (workspace: Workspace) => void }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [savingProfile, setSavingProfile] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const [startPosition, setStartPosition] = useState<'earliest' | 'latest'>('latest');
  const [description, setDescription] = useState(workspace?.description || '');
  const [savingDescription, setSavingDescription] = useState(false);
  useEffect(() => setDescription(workspace?.description || ''), [workspace?.description]);
  function checkDetail(check: WorkspaceReadiness['checks'][number]): string {
    if (check.code === 'kafka_topic') return String(check.details.topic || '');
    if (check.code === 'model_policy') {
      const missing = Array.isArray(check.details.missing_roles) ? check.details.missing_roles : [];
      return missing.length ? t('missingModelRoles', { roles: missing.map((role) => t(modelRoleKeys[role as keyof typeof modelRoleKeys])).join(', ') }) : t('modelPolicyHealthy');
    }
    if (check.code === 'repositories') return t('activeRepositoryCount', { count: Number(check.details.active_count || 0) });
    if (check.code === 'evidence_connectors') return t('healthyConnectorCount', { count: Number(check.details.healthy_count || 0) });
    return t('architectureContextEntryCount', { count: Number(check.details.entry_count || 0) });
  }
  async function changeProfile(profile: InvestigationPolicy['profile']) {
    if (!workspace || profile === policy?.profile) return;
    setSavingProfile(true);
    try { await updateInvestigationPolicy(workspace.id, profile); await onChanged(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSavingProfile(false); }
  }
  async function transition() {
    if (!workspace) return;
    setTransitioning(true);
    try {
      if (workspace.ingestion_state === 'draft') await startIngestion(workspace.id, startPosition);
      else if (workspace.ingestion_state === 'active') await pauseIngestion(workspace.id);
      else await resumeIngestion(workspace.id);
      await onChanged();
    } catch (cause) {
      toast.error(apiErrorMessage(cause, tc('requestFailed')));
      await onChanged();
    } finally { setTransitioning(false); }
  }
  async function saveDescription() {
    if (!workspace) return;
    setSavingDescription(true);
    try { onWorkspaceChanged(await updateWorkspace(workspace.id, { description })); }
    catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setSavingDescription(false); }
  }
  return <section className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-4 border-b pb-5"><div><p className="text-xs text-muted-foreground">{t('configurationState')}</p><div className="mt-2 flex items-center gap-2 text-sm font-medium">{readiness?.can_start ? <CheckCircle2 className="text-success" size={18} /> : <XCircle className="text-destructive" size={18} />}{readiness?.can_start ? t('ready') : t('notReady')}</div></div><div className="flex flex-wrap items-end gap-2">{workspace?.ingestion_state === 'draft' ? <label className="field"><span className="field-label">{t('startPosition')}</span><Select value={startPosition} disabled={transitioning} onChange={(event) => setStartPosition(event.target.value as 'earliest' | 'latest')}><option value="latest">{t('startPositions.latest')}</option><option value="earliest">{t('startPositions.earliest')}</option></Select></label> : null}<Button loading={transitioning} disabled={!workspace || (workspace.ingestion_state !== 'active' && !readiness?.can_start)} onClick={() => void transition()}>{workspace?.ingestion_state === 'active' ? <CirclePause size={16} /> : <CirclePlay size={16} />}{workspace?.ingestion_state === 'active' ? t('pauseListening') : workspace?.ingestion_state === 'paused' ? t('resumeListening') : t('startListening')}</Button></div></div>
    <div><h2 className="text-sm font-semibold">{t('readiness')}</h2><div className="mt-3 divide-y border-y">{readiness?.checks.map((check) => <div key={check.code} className="flex items-center gap-3 py-3 text-sm">{check.outcome === 'passed' ? <CheckCircle2 className="shrink-0 text-success" size={17} /> : check.outcome === 'blocked' ? <XCircle className="shrink-0 text-destructive" size={17} /> : <AlertTriangle className="shrink-0 text-warning-deep" size={17} />}<div className="min-w-0"><p className="font-medium">{t(readinessCheckKeys[check.code])}</p><p className="mt-0.5 break-words text-xs text-muted-foreground">{checkDetail(check)}</p></div><span className="ml-auto shrink-0 text-xs text-muted-foreground">{t(`readinessOutcomes.${check.outcome}`)}</span></div>)}</div></div>
    <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('ingestionRuntime')}</h2><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-4"><div><dt className="text-muted-foreground">{t('desiredState')}</dt><dd>{workspace ? t(`ingestionState.${workspace.ingestion_state}`) : '-'}</dd></div><div><dt className="text-muted-foreground">{t('observedState')}</dt><dd>{readiness ? t(`runtimeState.${readiness.runtime.observed_state}`) : '-'}</dd></div><div><dt className="text-muted-foreground">{t('assignedPartitions')}</dt><dd>{readiness?.runtime.assigned_partitions ?? 0}</dd></div><div><dt className="text-muted-foreground">{t('lastHeartbeat')}</dt><dd>{readiness?.runtime.last_heartbeat_at ? new Date(readiness.runtime.last_heartbeat_at).toLocaleString() : '-'}</dd></div></dl></div>
    <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('workspaceDescription')}</h2><div className="mt-3 max-w-3xl space-y-2"><Textarea value={description} maxLength={1000} onChange={(event) => setDescription(event.target.value)} /><div className="flex justify-end"><Button size="sm" loading={savingDescription} disabled={!workspace || description === workspace.description} onClick={() => void saveDescription()}>{tc('save')}</Button></div></div></div>
    <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('investigationDepth')}</h2><div className="mt-3 max-w-sm"><Select value={policy?.profile || ''} disabled={savingProfile} aria-busy={savingProfile} onChange={(event) => void changeProfile(event.target.value as InvestigationPolicy['profile'])}><option value="fast">{t('fast')}</option><option value="balanced">{t('balanced')}</option><option value="deep">{t('deep')}</option></Select><p className="mt-2 text-xs text-muted-foreground">{t('depthHelp', { revision: policy?.revision || '-' })}</p></div></div>
    <ArchitectureContextEditor workspaceId={workspace?.id} context={architectureContext} onChanged={onChanged} />
  </section>;
}

function ArchitectureContextEditor({ workspaceId, context, onChanged }: { workspaceId: number | undefined; context: WorkspaceArchitectureContext | null; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [entries, setEntries] = useState<WorkspaceArchitectureContext['entries']>(context?.entries || []);
  const [saving, setSaving] = useState(false);
  useEffect(() => setEntries(context?.entries || []), [context]);
  function update(index: number, value: WorkspaceArchitectureContext['entries'][number]) { setEntries((current) => current.map((entry, position) => position === index ? value : entry)); }
  async function save() { if (!workspaceId) return; setSaving(true); try { await updateWorkspaceArchitectureContext(workspaceId, entries); await onChanged(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSaving(false); } }
  const kinds: ArchitectureContextKind[] = ['system_purpose', 'architecture', 'critical_flow', 'dependency', 'operational_convention'];
  return <div className="border-t pt-5">
    <div className="flex items-center justify-between gap-3">
      <div><h2 className="text-sm font-semibold">{t('architectureContext')}</h2><p className="mt-1 text-xs text-muted-foreground">{t('contextRevision', { revision: context?.revision || 1 })}</p></div>
      <Button size="sm" variant="outline" onClick={() => setEntries((current) => [...current, { kind: 'architecture', title: '', content: '' }])}><Plus size={14} />{t('addContextEntry')}</Button>
    </div>
    <div className="mt-3 space-y-3">
      {entries.map((entry, index) => <div key={index} className="grid gap-2 border-y py-3 sm:grid-cols-[180px_1fr_32px]">
        <Select value={entry.kind} disabled={saving} onChange={(event) => update(index, { ...entry, kind: event.target.value as ArchitectureContextKind })}>{kinds.map((kind) => <option key={kind} value={kind}>{t(`architectureContextKinds.${kind}`)}</option>)}</Select>
        <div className="space-y-2"><Input value={entry.title} maxLength={120} placeholder={t('contextTitle')} onChange={(event) => update(index, { ...entry, title: event.target.value })} /><Textarea value={entry.content} maxLength={4000} placeholder={t('contextContent')} onChange={(event) => update(index, { ...entry, content: event.target.value })} /></div>
        <Button size="icon" variant="ghost" title={t('removeContextEntry')} aria-label={t('removeContextEntry')} onClick={() => setEntries((current) => current.filter((_, position) => position !== index))}><Trash2 size={15} /></Button>
      </div>)}
      {!entries.length ? <p className="border-y py-8 text-center text-sm text-muted-foreground">{t('noArchitectureContext')}</p> : null}
    </div>
    <div className="mt-3 flex justify-end"><Button size="sm" loading={saving} disabled={!workspaceId || entries.some((entry) => !entry.title.trim() || !entry.content.trim())} onClick={() => void save()}>{tc('save')}</Button></div>
  </div>;
}

function Models({ workspaceId, bindings, accountModels, onAdd, onChanged }: { workspaceId: string; bindings: ModelBinding[]; accountModels: ProviderAccountModel[]; onAdd: () => void; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [publishing, setPublishing] = useState(false);
  async function publish() {
    setPublishing(true); try {
      const active = bindings.filter((row) => row.state === 'active');
      await publishModelPolicy(workspaceId, { eligible_binding_ids: active.map((row) => row.id), role_policies: Object.fromEntries(roles.map((role) => [role, { execution_classes: ['latency_optimized', 'reasoning_optimized'] }])), verifier_policy: { required_for_confirmed: true }, pinned_evidence_kinds: ['incident_input', 'counter_evidence'], compression_levels: ['extractive', 'semantic'], minimum_output_tokens: 1024, provider_safety_margin_tokens: 512 });
      await onChanged();
    } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setPublishing(false); }
  }
  return <section className="space-y-4"><div className="flex flex-wrap justify-between gap-3"><p className="text-sm text-muted-foreground">{t('policyHelp')}</p><div className="flex gap-2"><Button size="sm" variant="outline" loading={publishing} loadingText={tc('saving')} onClick={() => void publish()} disabled={!bindings.length}>{t('publishPolicy')}</Button><Button size="sm" onClick={onAdd}><Plus size={15} />{t('addBinding')}</Button></div></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('accountModel')}</th><th>{t('roles')}</th><th>{t('classes')}</th><th>{t('budget')}</th><th>{t('revision')}</th></tr></thead><tbody>{bindings.map((row) => <tr key={row.id}><td>{accountModels.find((item) => item.id === row.provider_account_model_id)?.display_name || row.provider_account_model_id}</td><td>{row.allowed_roles.map((role) => t(modelRoleKeys[role as keyof typeof modelRoleKeys])).join(', ')}</td><td>{row.execution_classes.map((value) => t(executionClassKeys[value as keyof typeof executionClassKeys])).join(', ')}</td><td>{t('calls', { calls: row.max_calls })}</td><td>{row.revision}</td></tr>)}{!bindings.length ? <tr><td colSpan={5} className="py-8 text-center text-sm text-muted-foreground">{t('noModelBindings')}</td></tr> : null}</tbody></table></div></div></section>;
}

function Repositories({ workspaceId, rows, grants, buildUnits, components, analysis, onAuthorize, onBind, onChanged }: { workspaceId: string; rows: RepositoryBinding[]; grants: WorkspaceGitAccountGrant[]; buildUnits: BuildUnit[]; components: Component[]; analysis: RepositoryAnalysisJob | null; onAuthorize: () => void; onBind: () => void; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [startingAnalysis, setStartingAnalysis] = useState(false);
  async function analyze() { setStartingAnalysis(true); try { await startRepositoryAnalysis(workspaceId); await onChanged(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setStartingAnalysis(false); } }
  const analyzing = analysis?.state === 'queued' || analysis?.state === 'running';
  return <section className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2 text-sm text-muted-foreground"><ShieldCheck size={16} />{t('authorizedAccounts', { count: grants.filter((grant) => grant.state === 'active').length })}</div><div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" loading={startingAnalysis || analyzing} disabled={!rows.length || analyzing} onClick={() => void analyze()}><ScanSearch size={15} />{analysis?.state === 'succeeded' || analysis?.state === 'failed' ? t('reanalyzeRepositories') : t('analyzeRepositories')}</Button><Button size="sm" variant="outline" onClick={onAuthorize}><Link2 size={15} />{t('authorizeGitAccount')}</Button><Button size="sm" onClick={onBind}><Plus size={15} />{t('bindRepository')}</Button></div></div>
    {analysis ? <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-y py-3 text-sm"><span className="font-medium">{t('analysisState')}: {t(`repositoryAnalysisState.${analysis.state}`)}</span><span className="text-muted-foreground">{t('scannedFiles', { count: analysis.scanned_file_count })}</span><span className="text-muted-foreground">{t('analysisIssues', { count: analysis.issue_count })}</span>{analysis.failure_code ? <span className="text-destructive">{t(`repositoryAnalysisFailures.${analysis.failure_code}`)}</span> : null}</div> : null}
    <div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('provider')}</th><th>{t('role')}</th><th>{t('branch')}</th><th>{t('analyzedCommit')}</th></tr></thead><tbody>{rows.map((row) => { const revision = analysis?.source_revisions[String(row.id)]; return <tr key={row.id}><td className="font-medium" title={t('configurationRevision', { revision: row.revision })}><GitBranch className="mr-2 inline" size={15} />{row.full_name}</td><td>{row.provider_kind}</td><td>{t(repositoryRoleKeys[row.role as keyof typeof repositoryRoleKeys])}</td><td className="mono text-xs">{row.default_branch}</td><td className="mono text-xs">{revision ? revision.slice(0, 12) : '-'}</td></tr>; })}{!rows.length ? <tr><td colSpan={5} className="py-8 text-center text-sm text-muted-foreground">{t('noRepositories')}</td></tr> : null}</tbody></table></div></div>
    <div className="space-y-3 border-t pt-5"><h2 className="text-sm font-semibold">{t('detectedProjectStructure')}</h2>{analysis?.state === 'succeeded' ? <div className="grid gap-5 lg:grid-cols-2"><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('buildUnits')}</th><th>{t('buildSystem')}</th><th>{t('identity')}</th></tr></thead><tbody>{buildUnits.map((unit) => <tr key={unit.id}><td><span className="mono text-xs">{unit.source_root || unit.stable_key}</span><span className="mt-1 block text-xs text-muted-foreground">{unit.manifest_paths.join(', ')}</span></td><td>{t(buildSystemKeys[unit.build_system as keyof typeof buildSystemKeys])}</td><td>{t(identityStatusKeys[unit.identity_status])}</td></tr>)}{!buildUnits.length ? <tr><td colSpan={3} className="py-6 text-center text-sm text-muted-foreground">{t('noBuildUnits')}</td></tr> : null}</tbody></table></div></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('components')}</th><th>{t('kind')}</th><th>{t('buildUnits')}</th></tr></thead><tbody>{components.map((component) => <tr key={component.id}><td className="font-medium">{component.display_name}</td><td>{t(componentKindKeys[component.kind as keyof typeof componentKindKeys])}</td><td className="mono text-xs">{component.source_bindings.map((binding) => binding.build_unit_key).join(', ')}</td></tr>)}{!components.length ? <tr><td colSpan={3} className="py-6 text-center text-sm text-muted-foreground">{t('noComponents')}</td></tr> : null}</tbody></table></div></div></div> : <div className="border-y py-8 text-center text-sm text-muted-foreground">{analyzing ? t('repositoryAnalysisRunning') : t('repositoryAnalysisNotRun')}</div>}</div>
  </section>;
}

function Connectors({ workspaceId, rows, onAdd, onChanged }: { workspaceId: string; rows: EvidenceConnector[]; onAdd: () => void; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [busy, setBusy] = useState<string | null>(null);
  async function runAction(key: string, action: () => Promise<unknown>) { setBusy(key); try { await action(); await onChanged(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setBusy(null); } }
  return <section className="space-y-4"><div className="flex justify-end"><Button size="sm" onClick={onAdd}><Plus size={15} />{t('addConnector')}</Button></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('kind')}</th><th>{t('capabilities')}</th><th>{t('verification')}</th><th>{t('secrets')}</th><th /></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="font-medium"><Database className="mr-2 inline" size={15} />{row.name}</td><td>{t(`connectorKinds.${row.kind}`)}</td><td>{row.capabilities.map((capability) => t(connectorCapabilityKeys[capability as keyof typeof connectorCapabilityKeys])).join(', ')}</td><td>{t(`verificationState.${row.verification_status}`)}</td><td>{row.configured_secret_fields.map((field) => field === 'api_key' ? 'API Key' : t(`secretFields.${field}`)).join(', ') || t('none')}</td><td><div className="flex justify-end"><Button size="icon" variant="ghost" loading={busy === `verify-${row.id}`} disabled={busy !== null} title={t('verify')} aria-label={t('verify')} onClick={() => void runAction(`verify-${row.id}`, () => testConnector(workspaceId, row.id))}><Activity size={15} /></Button><Button size="icon" variant="ghost" loading={busy === `introspect-${row.id}`} title={t('introspect')} aria-label={t('introspect')} disabled={busy !== null || row.verification_status !== 'healthy'} onClick={() => void runAction(`introspect-${row.id}`, () => introspectConnector(workspaceId, row.id))}><ScanSearch size={15} /></Button></div></td></tr>)}{!rows.length ? <tr><td colSpan={6} className="py-8 text-center text-sm text-muted-foreground">{t('noConnectors')}</td></tr> : null}</tbody></table></div></div></section>;
}

function BindingDialog({ open, onOpenChange, workspaceId, accountModels, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; accountModels: ProviderAccountModel[]; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [accountModel, setAccountModel] = useState(''); const [selectedRoles, setRoles] = useState<string[]>(roles);
  const [saving, setSaving] = useState(false);
  async function create() { setSaving(true); try { await createModelBinding(workspaceId, { provider_account_model_id: Number(accountModel), execution_classes: ['latency_optimized', 'reasoning_optimized'], allowed_roles: selectedRoles, priority: 0, max_calls: 16, max_cost_per_call: 5, timeout_ms: 60000, allowed_data_classes: ['masked_operational', 'source_code'], max_context_utilization: 0.8 }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSaving(false); } }
  return <Dialog open={open} onOpenChange={(value) => !saving && onOpenChange(value)}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('addModelBinding')}</DialogTitle></DialogHeader><Select value={accountModel} disabled={saving} onChange={(event) => setAccountModel(event.target.value)}><option value="">{t('selectAccountModel')}</option>{accountModels.filter((row) => row.state === 'active').map((row) => <option key={row.id} value={row.id}>{row.display_name}</option>)}</Select><fieldset disabled={saving} className="grid gap-2 sm:grid-cols-2"><legend className="mb-2 text-sm font-medium">{t('allowedRoles')}</legend>{roles.map((role) => <label key={role} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selectedRoles.includes(role)} onChange={(event) => setRoles((current) => event.target.checked ? [...current, role] : current.filter((item) => item !== role))} />{t(modelRoleKeys[role as keyof typeof modelRoleKeys])}</label>)}</fieldset><DialogFooter><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={!accountModel || !selectedRoles.length} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}

function RepositoryDialog({ open, onOpenChange, workspaceId, candidates, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; candidates: WorkspaceRepositoryCandidate[]; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [candidateId, setCandidateId] = useState(''); const [role, setRole] = useState('runtime_source');
  const [description, setDescription] = useState('');
  const [search, setSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const normalizedSearch = search.trim().toLowerCase();
  const activeCandidates = candidates.filter((candidate) => !candidate.archived && (!normalizedSearch || `${candidate.full_name} ${candidate.account_name} ${candidate.provider_kind}`.toLowerCase().includes(normalizedSearch)));
  async function create() { setSaving(true); try { await bindRepository(workspaceId, { repository_entitlement_id: Number(candidateId), role, priority: 0, description }); onOpenChange(false); setDescription(''); await onCreated(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSaving(false); } }
  return <Dialog open={open} onOpenChange={(value) => !saving && onOpenChange(value)}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('bindRepository')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('searchRepositories')}</span><Input type="search" disabled={saving} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('searchRepositoriesPlaceholder')} /></label><label className="field"><span className="field-label">{t('repository')}</span><Select value={candidateId} disabled={saving} onChange={(event) => setCandidateId(event.target.value)}><option value="">{t('selectRepository')}</option>{activeCandidates.map((candidate) => <option key={candidate.entitlement_id} value={candidate.entitlement_id}>{candidate.full_name} ({candidate.account_name})</option>)}</Select></label><label className="field"><span className="field-label">{t('role')}</span><Select value={role} disabled={saving} onChange={(event) => setRole(event.target.value)}>{repositoryRoles.map((value) => <option key={value} value={value}>{t(repositoryRoleKeys[value])}</option>)}</Select></label><label className="field"><span className="field-label">{t('repositoryDescription')}</span><Textarea value={description} maxLength={2000} disabled={saving} onChange={(event) => setDescription(event.target.value)} /></label>{!activeCandidates.length ? <p className="text-sm text-muted-foreground">{search ? t('noRepositorySearchResults') : t('noAuthorizedRepositories')}</p> : null}</div><DialogFooter><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={!candidateId} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}

function GrantDialog({ open, onOpenChange, workspaceId, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [accounts, setAccounts] = useState<GitAccount[]>([]); const [repositories, setRepositories] = useState<GitAccountRepository[]>([]); const [accountId, setAccountId] = useState(''); const [scope, setScope] = useState<'selected' | 'all_visible'>('selected'); const [selected, setSelected] = useState<number[]>([]); const [search, setSearch] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (!open) return; void fetchGitAccounts().then(setAccounts).catch((cause) => toast.error(apiErrorMessage(cause, tc('requestFailed')))); }, [open, tc]);
  useEffect(() => { if (!accountId) { setRepositories([]); setSelected([]); return; } void fetchGitAccountRepositories(Number(accountId)).then((values) => { setRepositories(values); setSelected([]); }).catch((cause) => toast.error(apiErrorMessage(cause, tc('requestFailed')))); }, [accountId, tc]);
  async function create() { setSaving(true); try { await createWorkspaceGitAccountGrant(workspaceId, { account_connection_id: Number(accountId), repository_scope: scope, repository_ids: scope === 'selected' ? selected : [] }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSaving(false); } }
  function toggle(repositoryId: number, checked: boolean) { setSelected((current) => checked ? [...current, repositoryId] : current.filter((value) => value !== repositoryId)); }
  const filtered = repositories.filter((repository) => !search.trim() || `${repository.full_name} ${repository.visibility}`.toLowerCase().includes(search.trim().toLowerCase()));
  const visibleIds = filtered.filter((repository) => !repository.archived).map((repository) => repository.repository_id);
  return <Dialog open={open} onOpenChange={(value) => !saving && onOpenChange(value)}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('authorizeGitAccount')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('gitAccount')}</span><Select value={accountId} disabled={saving} onChange={(event) => setAccountId(event.target.value)}><option value="">{t('selectGitAccount')}</option>{accounts.filter((account) => account.state === 'active' && account.verification_status === 'healthy').map((account) => <option key={account.id} value={account.id}>{account.adapter_id} / {account.name} ({account.repository_count})</option>)}</Select></label><label className="field"><span className="field-label">{t('repositoryAccess')}</span><Select value={scope} disabled={saving} onChange={(event) => setScope(event.target.value as 'selected' | 'all_visible')}><option value="all_visible">{t('allVisibleRepositories')}</option><option value="selected">{t('selectedRepositories')}</option></Select></label>{scope === 'selected' ? <><Input type="search" disabled={saving} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('searchRepositoriesPlaceholder')} /><div className="flex items-center justify-between text-xs text-muted-foreground"><span>{t('repositoriesSelected', { count: selected.length })}</span><div className="flex gap-2"><button disabled={saving} className="hover:text-foreground disabled:opacity-50" onClick={() => setSelected((current) => [...new Set([...current, ...visibleIds])])}>{t('selectVisible')}</button><button disabled={saving} className="hover:text-foreground disabled:opacity-50" onClick={() => setSelected((current) => current.filter((id) => !visibleIds.includes(id)))}>{t('clearVisible')}</button></div></div><div className="max-h-56 overflow-auto border">{filtered.map((repository) => <label key={repository.repository_id} className="flex items-center gap-2 border-b px-3 py-2 text-sm last:border-0"><input type="checkbox" checked={selected.includes(repository.repository_id)} onChange={(event) => toggle(repository.repository_id, event.target.checked)} disabled={saving || repository.archived} />{repository.full_name}<span className="ml-auto text-xs text-muted-foreground">{t(`visibility.${repository.visibility}`)}</span></label>)}{!filtered.length ? <p className="px-3 py-8 text-center text-sm text-muted-foreground">{t('noRepositorySearchResults')}</p> : null}</div></> : null}</div><DialogFooter><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={!accountId || (scope === 'selected' && !selected.length)} onClick={() => void create()}>{t('authorize')}</Button></DialogFooter></DialogContent></Dialog>;
}

function splitValues(value: string): string[] { return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean); }
