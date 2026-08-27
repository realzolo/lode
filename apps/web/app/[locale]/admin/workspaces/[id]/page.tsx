'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, Database, GitBranch, Link2, Plus, RefreshCw, ScanSearch, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Tabs } from '@/components/ui/tabs';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { EvidenceConnectorDialog as ConnectorDialog } from '@/components/evidence-connector-dialog';
import {
  bindRepository,
  apiErrorMessage,
  fetchBuildUnits,
  createModelBinding,
  createWorkspaceGitAccountGrant,
  fetchCapabilities,
  fetchComponents,
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
  Workspace,
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
const capabilityGapKeys = { model_policy: 'capabilityGapKinds.model_policy', repositories: 'capabilityGapKinds.repositories', evidence_connectors: 'capabilityGapKinds.evidence_connectors' } as const;
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
  const [capabilities, setCapabilities] = useState<{ models: number; repositories: number; healthy_connectors: number; gaps: string[] } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dialog, setDialog] = useState<'binding' | 'repository' | 'grant' | 'connector' | null>(null);

  const load = useCallback(async () => {
    try {
      const [ws, modelRows, accountRows, repoRows, candidateRows, grantRows, buildUnitRows, componentRows, connectorRows, kindRows, caps, policy, memberRows, userRows] = await Promise.all([
        fetchWorkspace(params.id),
        fetchModelBindings(params.id),
        fetchProviderAccounts(),
        fetchRepositories(params.id),
        fetchWorkspaceRepositoryCandidates(params.id),
        fetchWorkspaceGitAccountGrants(params.id),
        fetchBuildUnits(params.id),
        fetchComponents(params.id),
        fetchConnectors(params.id),
        fetchConnectorKinds(),
        fetchCapabilities(params.id),
        fetchInvestigationPolicy(params.id),
        fetchWorkspaceMembers(params.id),
        fetchUsers(),
      ]);
      setWorkspace(ws);
      setBindings(modelRows);
      setAccountModels(flattenAccountModels(accountRows));
      setRepositories(repoRows);
      setCandidates(candidateRows);
      setGrants(grantRows);
      setBuildUnits(buildUnitRows.items);
      setComponents(componentRows.items);
      setConnectors(connectorRows);
      setKinds(kindRows);
      setCapabilities(caps);
      setInvestigationPolicy(policy);
      setMembers(memberRows);
      setUsers(userRows.filter((user) => !user.is_system_admin));
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally { setLoading(false); }
  }, [params.id, tc]);

  useEffect(() => { void load(); }, [load]);

  const tabs = useMemo(() => [
    { value: 'overview', label: t('overview'), content: <Overview workspace={workspace} capabilities={capabilities} policy={investigationPolicy} onPolicyChanged={setInvestigationPolicy} /> },
    { value: 'models', label: t('modelPolicy'), content: <Models workspaceId={params.id} bindings={bindings} accountModels={accountModels} onAdd={() => setDialog('binding')} onChanged={load} /> },
    { value: 'repositories', label: t('repositories'), content: <Repositories rows={repositories} grants={grants} buildUnits={buildUnits} components={components} onAuthorize={() => setDialog('grant')} onBind={() => setDialog('repository')} /> },
    { value: 'connectors', label: t('connectors'), content: <Connectors workspaceId={params.id} rows={connectors} onAdd={() => setDialog('connector')} onChanged={load} /> },
    { value: 'members', label: t('members'), content: <Members workspaceId={params.id} members={members} users={users} onChanged={load} /> },
  ], [accountModels, bindings, buildUnits, capabilities, components, connectors, grants, investigationPolicy, load, members, params.id, repositories, t, users, workspace]);

  if (loading) return <main className="space-y-6"><div className="h-16 border-b" /><ListSkeleton rows={7} columns={5} /></main>;

  return <main className="space-y-6">
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

function Overview({ workspace, capabilities, policy, onPolicyChanged }: { workspace: Workspace | null; capabilities: { models: number; repositories: number; healthy_connectors: number; gaps: string[] } | null; policy: InvestigationPolicy | null; onPolicyChanged: (policy: InvestigationPolicy) => void }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [savingProfile, setSavingProfile] = useState(false);
  const stats = [[t('modelBindings'), capabilities?.models ?? 0], [t('repositories'), capabilities?.repositories ?? 0], [t('healthyConnectors'), capabilities?.healthy_connectors ?? 0]];
  async function changeProfile(profile: InvestigationPolicy['profile']) {
    if (!workspace || profile === policy?.profile) return;
    setSavingProfile(true);
    try { onPolicyChanged(await updateInvestigationPolicy(workspace.id, profile)); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSavingProfile(false); }
  }
  return <section className="space-y-5">
    <div className="grid gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-3">{stats.map(([label, value]) => <div key={label} className="bg-card p-5"><p className="text-xs text-muted-foreground">{label}</p><strong className="mt-2 block text-2xl">{value}</strong></div>)}</div>
    <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('investigationDepth')}</h2><div className="mt-3 max-w-sm"><Select value={policy?.profile || ''} disabled={savingProfile} aria-busy={savingProfile} onChange={(event) => void changeProfile(event.target.value as InvestigationPolicy['profile'])}><option value="fast">{t('fast')}</option><option value="balanced">{t('balanced')}</option><option value="deep">{t('deep')}</option></Select><p className="mt-2 text-xs text-muted-foreground">{t('depthHelp', { revision: policy?.revision || '-' })}</p></div></div>
    <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('ingestion')}</h2><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="text-muted-foreground">{t('state')}</dt><dd>{workspace ? t(`ingestionState.${workspace.ingestion_state}`) : '-'}</dd></div><div><dt className="text-muted-foreground">{t('version')}</dt><dd>{workspace?.ingestion_version}</dd></div><div><dt className="text-muted-foreground">{t('startPosition')}</dt><dd>{workspace?.ingestion_start_position ? t(`startPositions.${workspace.ingestion_start_position}`) : t('notStarted')}</dd></div></dl></div>
    {capabilities?.gaps.length ? <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('capabilityGaps')}</h2><div className="mt-2 flex flex-wrap gap-2">{capabilities.gaps.map((gap) => <span key={gap} className="rounded-sm bg-warning/10 px-2 py-1 text-xs text-warning-deep">{t(capabilityGapKeys[gap as keyof typeof capabilityGapKeys])}</span>)}</div></div> : null}
  </section>;
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

function Repositories({ rows, grants, buildUnits, components, onAuthorize, onBind }: { rows: RepositoryBinding[]; grants: WorkspaceGitAccountGrant[]; buildUnits: BuildUnit[]; components: Component[]; onAuthorize: () => void; onBind: () => void }) {
  const t = useTranslations('workspace');
  return <section className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2 text-sm text-muted-foreground"><ShieldCheck size={16} />{t('authorizedAccounts', { count: grants.filter((grant) => grant.state === 'active').length })}</div><div className="flex gap-2"><Button size="sm" variant="outline" onClick={onAuthorize}><Link2 size={15} />{t('authorizeGitAccount')}</Button><Button size="sm" onClick={onBind}><Plus size={15} />{t('bindRepository')}</Button></div></div>
    <div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('provider')}</th><th>{t('role')}</th><th>{t('branch')}</th><th>{t('revision')}</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="font-medium"><GitBranch className="mr-2 inline" size={15} />{row.full_name}</td><td>{row.provider_kind}</td><td>{t(repositoryRoleKeys[row.role as keyof typeof repositoryRoleKeys])}</td><td className="mono text-xs">{row.default_branch}</td><td>{row.revision}</td></tr>)}{!rows.length ? <tr><td colSpan={5} className="py-8 text-center text-sm text-muted-foreground">{t('noRepositories')}</td></tr> : null}</tbody></table></div></div>
    <div className="space-y-3 border-t pt-5"><h2 className="text-sm font-semibold">{t('derivedArchitecture')}</h2><div className="grid gap-5 lg:grid-cols-2"><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('buildUnits')}</th><th>{t('buildSystem')}</th><th>{t('identity')}</th></tr></thead><tbody>{buildUnits.map((unit) => <tr key={unit.id}><td className="mono text-xs">{unit.source_root || unit.stable_key}</td><td>{t(buildSystemKeys[unit.build_system as keyof typeof buildSystemKeys])}</td><td>{t(identityStatusKeys[unit.identity_status])}</td></tr>)}{!buildUnits.length ? <tr><td colSpan={3} className="py-6 text-center text-sm text-muted-foreground">{t('noBuildUnits')}</td></tr> : null}</tbody></table></div></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('components')}</th><th>{t('kind')}</th><th>{t('buildUnits')}</th></tr></thead><tbody>{components.map((component) => <tr key={component.id}><td className="font-medium">{component.display_name}</td><td>{t(componentKindKeys[component.kind as keyof typeof componentKindKeys])}</td><td className="mono text-xs">{component.source_bindings.map((binding) => binding.build_unit_key).join(', ')}</td></tr>)}{!components.length ? <tr><td colSpan={3} className="py-6 text-center text-sm text-muted-foreground">{t('noComponents')}</td></tr> : null}</tbody></table></div></div></div></div>
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
  const [search, setSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const normalizedSearch = search.trim().toLowerCase();
  const activeCandidates = candidates.filter((candidate) => !candidate.archived && (!normalizedSearch || `${candidate.full_name} ${candidate.account_name} ${candidate.provider_kind}`.toLowerCase().includes(normalizedSearch)));
  async function create() { setSaving(true); try { await bindRepository(workspaceId, { repository_entitlement_id: Number(candidateId), role, priority: 0, description: '' }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSaving(false); } }
  return <Dialog open={open} onOpenChange={(value) => !saving && onOpenChange(value)}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('bindRepository')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('searchRepositories')}</span><Input type="search" disabled={saving} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('searchRepositoriesPlaceholder')} /></label><label className="field"><span className="field-label">{t('repository')}</span><Select value={candidateId} disabled={saving} onChange={(event) => setCandidateId(event.target.value)}><option value="">{t('selectRepository')}</option>{activeCandidates.map((candidate) => <option key={candidate.entitlement_id} value={candidate.entitlement_id}>{candidate.full_name} ({candidate.account_name})</option>)}</Select></label><label className="field"><span className="field-label">{t('role')}</span><Select value={role} disabled={saving} onChange={(event) => setRole(event.target.value)}>{repositoryRoles.map((value) => <option key={value} value={value}>{t(repositoryRoleKeys[value])}</option>)}</Select></label>{!activeCandidates.length ? <p className="text-sm text-muted-foreground">{search ? t('noRepositorySearchResults') : t('noAuthorizedRepositories')}</p> : null}</div><DialogFooter><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={!candidateId} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
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
