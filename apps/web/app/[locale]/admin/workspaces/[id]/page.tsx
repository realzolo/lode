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
import { Textarea } from '@/components/ui/textarea';
import {
  bindRepository,
  fetchBuildUnits,
  createConnector,
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
const repositoryRoles = ['runtime_source', 'shared_library', 'infrastructure', 'documentation'];

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
      setError(String(cause));
    }
  }, [params.id]);

  useEffect(() => { void load(); }, [load]);

  const tabs = useMemo(() => [
    { value: 'overview', label: t('overview'), content: <Overview workspace={workspace} capabilities={capabilities} policy={investigationPolicy} onPolicyChanged={setInvestigationPolicy} /> },
    { value: 'models', label: t('modelPolicy'), content: <Models workspaceId={params.id} bindings={bindings} accountModels={accountModels} onAdd={() => setDialog('binding')} onChanged={load} /> },
    { value: 'repositories', label: t('repositories'), content: <Repositories rows={repositories} grants={grants} buildUnits={buildUnits} components={components} onAuthorize={() => setDialog('grant')} onBind={() => setDialog('repository')} /> },
    { value: 'connectors', label: t('connectors'), content: <Connectors workspaceId={params.id} rows={connectors} onAdd={() => setDialog('connector')} onChanged={load} /> },
    { value: 'members', label: t('members'), content: <Members workspaceId={params.id} members={members} users={users} onChanged={load} /> },
  ], [accountModels, bindings, buildUnits, capabilities, components, connectors, grants, investigationPolicy, load, members, params.id, repositories, t, users, workspace]);

  return <main className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="mb-2 text-sm text-muted-foreground"><Link href="/admin" className="hover:text-link">{t('workspace')}</Link> / {params.id}</p>
        <h1 className="page-title">{workspace?.name || t('workspace')}</h1>
        <p className="page-subtitle mono">{workspace?.ingestion_topic}</p>
      </div>
      <Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
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
  const [userId, setUserId] = useState('');
  const [permission, setPermission] = useState<'viewer' | 'operator'>('viewer');
  async function save() { if (!userId) return; try { await putWorkspaceMember(workspaceId, Number(userId), permission); await onChanged(); setUserId(''); } catch (cause) { toast.error(String(cause)); } }
  async function revoke(memberId: number) { try { await removeWorkspaceMember(workspaceId, memberId); await onChanged(); } catch (cause) { toast.error(String(cause)); } }
  return <section className="space-y-4"><div className="flex flex-wrap gap-2"><Select value={userId} onChange={(event) => setUserId(event.target.value)}><option value="">{t('selectUser')}</option>{users.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}</Select><Select value={permission} onChange={(event) => setPermission(event.target.value as 'viewer' | 'operator')}><option value="viewer">{t('viewer')}</option><option value="operator">{t('operator')}</option></Select><Button disabled={!userId} onClick={() => void save()}>{t('grant')}</Button></div><div className="table-wrap"><table className="table"><thead><tr><th>{t('member')}</th><th>{t('permissions')}</th><th /></tr></thead><tbody>{members.map((member) => <tr key={member.user_id}><td>{member.username}</td><td><Select value={member.permission} onChange={(event) => void putWorkspaceMember(workspaceId, member.user_id, event.target.value as 'viewer' | 'operator').then(onChanged)}><option value="viewer">{t('viewer')}</option><option value="operator">{t('operator')}</option></Select></td><td><Button size="sm" variant="outline" onClick={() => void revoke(member.user_id)}>{t('revoke')}</Button></td></tr>)}</tbody></table></div></section>;
}

function Overview({ workspace, capabilities, policy, onPolicyChanged }: { workspace: Workspace | null; capabilities: { models: number; repositories: number; healthy_connectors: number; gaps: string[] } | null; policy: InvestigationPolicy | null; onPolicyChanged: (policy: InvestigationPolicy) => void }) {
  const t = useTranslations('workspace');
  const stats = [[t('modelBindings'), capabilities?.models ?? 0], [t('repositories'), capabilities?.repositories ?? 0], [t('healthyConnectors'), capabilities?.healthy_connectors ?? 0]];
  async function changeProfile(profile: InvestigationPolicy['profile']) {
    if (!workspace || profile === policy?.profile) return;
    try { onPolicyChanged(await updateInvestigationPolicy(workspace.id, profile)); } catch (cause) { toast.error(String(cause)); }
  }
  return <section className="space-y-5">
    <div className="grid gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-3">{stats.map(([label, value]) => <div key={label} className="bg-card p-5"><p className="text-xs text-muted-foreground">{label}</p><strong className="mt-2 block text-2xl">{value}</strong></div>)}</div>
    <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('investigationDepth')}</h2><div className="mt-3 max-w-sm"><Select value={policy?.profile || ''} onChange={(event) => void changeProfile(event.target.value as InvestigationPolicy['profile'])}><option value="fast">{t('fast')}</option><option value="balanced">{t('balanced')}</option><option value="deep">{t('deep')}</option></Select><p className="mt-2 text-xs text-muted-foreground">{t('depthHelp', { revision: policy?.revision || '-' })}</p></div></div>
    <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('ingestion')}</h2><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="text-muted-foreground">{t('state')}</dt><dd>{workspace?.ingestion_state}</dd></div><div><dt className="text-muted-foreground">{t('version')}</dt><dd>{workspace?.ingestion_version}</dd></div><div><dt className="text-muted-foreground">{t('startPosition')}</dt><dd>{workspace?.ingestion_start_position || t('notStarted')}</dd></div></dl></div>
    {capabilities?.gaps.length ? <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('capabilityGaps')}</h2><div className="mt-2 flex flex-wrap gap-2">{capabilities.gaps.map((gap) => <span key={gap} className="rounded-sm bg-warning/10 px-2 py-1 text-xs text-warning-deep">{gap}</span>)}</div></div> : null}
  </section>;
}

function Models({ workspaceId, bindings, accountModels, onAdd, onChanged }: { workspaceId: string; bindings: ModelBinding[]; accountModels: ProviderAccountModel[]; onAdd: () => void; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace');
  async function publish() {
    try {
      const active = bindings.filter((row) => row.state === 'active');
      await publishModelPolicy(workspaceId, { eligible_binding_ids: active.map((row) => row.id), role_policies: Object.fromEntries(roles.map((role) => [role, { execution_classes: ['latency_optimized', 'reasoning_optimized'] }])), verifier_policy: { required_for_confirmed: true }, pinned_evidence_kinds: ['incident_input', 'counter_evidence'], compression_levels: ['extractive', 'semantic'], minimum_output_tokens: 1024, provider_safety_margin_tokens: 512 });
      await onChanged();
    } catch (cause) { toast.error(String(cause)); }
  }
  return <section className="space-y-4"><div className="flex flex-wrap justify-between gap-3"><p className="text-sm text-muted-foreground">{t('policyHelp')}</p><div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => void publish()} disabled={!bindings.length}>{t('publishPolicy')}</Button><Button size="sm" onClick={onAdd}><Plus size={15} />{t('addBinding')}</Button></div></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('accountModel')}</th><th>{t('roles')}</th><th>{t('classes')}</th><th>{t('budget')}</th><th>{t('revision')}</th></tr></thead><tbody>{bindings.map((row) => <tr key={row.id}><td>{accountModels.find((item) => item.id === row.provider_account_model_id)?.display_name || row.provider_account_model_id}</td><td>{row.allowed_roles.join(', ')}</td><td>{row.execution_classes.join(', ')}</td><td>{t('calls', { calls: row.max_calls })}</td><td>{row.revision}</td></tr>)}</tbody></table></div></div></section>;
}

function Repositories({ rows, grants, buildUnits, components, onAuthorize, onBind }: { rows: RepositoryBinding[]; grants: WorkspaceGitAccountGrant[]; buildUnits: BuildUnit[]; components: Component[]; onAuthorize: () => void; onBind: () => void }) {
  const t = useTranslations('workspace');
  return <section className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2 text-sm text-muted-foreground"><ShieldCheck size={16} />{t('authorizedAccounts', { count: grants.filter((grant) => grant.state === 'active').length })}</div><div className="flex gap-2"><Button size="sm" variant="outline" onClick={onAuthorize}><Link2 size={15} />{t('authorizeGitAccount')}</Button><Button size="sm" onClick={onBind}><Plus size={15} />{t('bindRepository')}</Button></div></div>
    <div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('provider')}</th><th>{t('role')}</th><th>{t('branch')}</th><th>{t('revision')}</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="font-medium"><GitBranch className="mr-2 inline" size={15} />{row.full_name}</td><td>{row.provider_kind}</td><td>{t(row.role)}</td><td className="mono text-xs">{row.default_branch}</td><td>{row.revision}</td></tr>)}{!rows.length ? <tr><td colSpan={5} className="py-8 text-center text-sm text-muted-foreground">{t('noRepositories')}</td></tr> : null}</tbody></table></div></div>
    <div className="space-y-3 border-t pt-5"><h2 className="text-sm font-semibold">{t('derivedArchitecture')}</h2><div className="grid gap-5 lg:grid-cols-2"><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('buildUnits')}</th><th>{t('buildSystem')}</th><th>{t('identity')}</th></tr></thead><tbody>{buildUnits.map((unit) => <tr key={unit.id}><td className="mono text-xs">{unit.source_root || unit.stable_key}</td><td>{unit.build_system}</td><td>{unit.identity_status}</td></tr>)}{!buildUnits.length ? <tr><td colSpan={3} className="py-6 text-center text-sm text-muted-foreground">{t('noBuildUnits')}</td></tr> : null}</tbody></table></div></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('components')}</th><th>{t('kind')}</th><th>{t('buildUnits')}</th></tr></thead><tbody>{components.map((component) => <tr key={component.id}><td className="font-medium">{component.display_name}</td><td>{component.kind}</td><td className="mono text-xs">{component.source_bindings.map((binding) => binding.build_unit_key).join(', ')}</td></tr>)}{!components.length ? <tr><td colSpan={3} className="py-6 text-center text-sm text-muted-foreground">{t('noComponents')}</td></tr> : null}</tbody></table></div></div></div></div>
  </section>;
}

function Connectors({ workspaceId, rows, onAdd, onChanged }: { workspaceId: string; rows: EvidenceConnector[]; onAdd: () => void; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace');
  async function runAction(action: () => Promise<unknown>) { try { await action(); await onChanged(); } catch (cause) { toast.error(String(cause)); } }
  return <section className="space-y-4"><div className="flex justify-end"><Button size="sm" onClick={onAdd}><Plus size={15} />{t('addConnector')}</Button></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('kind')}</th><th>{t('capabilities')}</th><th>{t('verification')}</th><th>{t('secrets')}</th><th /></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="font-medium"><Database className="mr-2 inline" size={15} />{row.name}</td><td>{row.kind}</td><td>{row.capabilities.join(', ')}</td><td>{row.verification_status}</td><td>{row.configured_secret_fields.join(', ') || t('none')}</td><td><div className="flex justify-end"><Button size="icon" variant="ghost" title={t('verify')} aria-label={t('verify')} onClick={() => void runAction(() => testConnector(workspaceId, row.id))}><Activity size={15} /></Button><Button size="icon" variant="ghost" title={t('introspect')} aria-label={t('introspect')} disabled={row.verification_status !== 'healthy'} onClick={() => void runAction(() => introspectConnector(workspaceId, row.id))}><ScanSearch size={15} /></Button></div></td></tr>)}{!rows.length ? <tr><td colSpan={6} className="py-8 text-center text-sm text-muted-foreground">{t('noConnectors')}</td></tr> : null}</tbody></table></div></div></section>;
}

function BindingDialog({ open, onOpenChange, workspaceId, accountModels, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; accountModels: ProviderAccountModel[]; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [accountModel, setAccountModel] = useState(''); const [selectedRoles, setRoles] = useState<string[]>(roles);
  async function create() { try { await createModelBinding(workspaceId, { provider_account_model_id: Number(accountModel), execution_classes: ['latency_optimized', 'reasoning_optimized'], allowed_roles: selectedRoles, priority: 0, max_calls: 16, max_cost_per_call: 5, timeout_ms: 60000, allowed_data_classes: ['masked_operational', 'source_code'], max_context_utilization: 0.8 }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>{t('addModelBinding')}</DialogTitle></DialogHeader><Select value={accountModel} onChange={(event) => setAccountModel(event.target.value)}><option value="">{t('selectAccountModel')}</option>{accountModels.filter((row) => row.state === 'active').map((row) => <option key={row.id} value={row.id}>{row.display_name}</option>)}</Select><fieldset className="grid gap-2 sm:grid-cols-2"><legend className="mb-2 text-sm font-medium">{t('allowedRoles')}</legend>{roles.map((role) => <label key={role} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selectedRoles.includes(role)} onChange={(event) => setRoles((current) => event.target.checked ? [...current, role] : current.filter((item) => item !== role))} />{role}</label>)}</fieldset><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!accountModel || !selectedRoles.length} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}

function RepositoryDialog({ open, onOpenChange, workspaceId, candidates, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; candidates: WorkspaceRepositoryCandidate[]; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [candidateId, setCandidateId] = useState(''); const [role, setRole] = useState('runtime_source');
  const activeCandidates = candidates.filter((candidate) => !candidate.archived);
  async function create() { try { await bindRepository(workspaceId, { repository_entitlement_id: Number(candidateId), role, priority: 0, description: '' }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>{t('bindRepository')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('repository')}</span><Select value={candidateId} onChange={(event) => setCandidateId(event.target.value)}><option value="">{t('selectRepository')}</option>{activeCandidates.map((candidate) => <option key={candidate.entitlement_id} value={candidate.entitlement_id}>{candidate.full_name} ({candidate.account_name})</option>)}</Select></label><label className="field"><span className="field-label">{t('role')}</span><Select value={role} onChange={(event) => setRole(event.target.value)}>{repositoryRoles.map((value) => <option key={value} value={value}>{t(value)}</option>)}</Select></label>{!activeCandidates.length ? <p className="text-sm text-muted-foreground">{t('noAuthorizedRepositories')}</p> : null}</div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!candidateId} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}

function GrantDialog({ open, onOpenChange, workspaceId, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [accounts, setAccounts] = useState<GitAccount[]>([]); const [repositories, setRepositories] = useState<GitAccountRepository[]>([]); const [accountId, setAccountId] = useState(''); const [scope, setScope] = useState<'selected' | 'all_visible'>('selected'); const [selected, setSelected] = useState<number[]>([]);
  useEffect(() => { if (!open) return; void fetchGitAccounts().then(setAccounts).catch((cause) => toast.error(String(cause))); }, [open]);
  useEffect(() => { if (!accountId) { setRepositories([]); setSelected([]); return; } void fetchGitAccountRepositories(Number(accountId)).then((values) => { setRepositories(values); setSelected([]); }).catch((cause) => toast.error(String(cause))); }, [accountId]);
  async function create() { try { await createWorkspaceGitAccountGrant(workspaceId, { account_connection_id: Number(accountId), repository_scope: scope, repository_ids: scope === 'selected' ? selected : [] }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  function toggle(repositoryId: number, checked: boolean) { setSelected((current) => checked ? [...current, repositoryId] : current.filter((value) => value !== repositoryId)); }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>{t('authorizeGitAccount')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('gitAccount')}</span><Select value={accountId} onChange={(event) => setAccountId(event.target.value)}><option value="">{t('selectGitAccount')}</option>{accounts.filter((account) => account.state === 'active' && account.verification_status === 'healthy').map((account) => <option key={account.id} value={account.id}>{account.adapter_id} / {account.name} ({account.repository_count})</option>)}</Select></label><label className="field"><span className="field-label">{t('repositoryAccess')}</span><Select value={scope} onChange={(event) => setScope(event.target.value as 'selected' | 'all_visible')}><option value="all_visible">{t('allVisibleRepositories')}</option><option value="selected">{t('selectedRepositories')}</option></Select></label>{scope === 'selected' && repositories.length ? <div className="max-h-48 overflow-auto rounded-md border">{repositories.map((repository) => <label key={repository.repository_id} className="flex items-center gap-2 border-b px-3 py-2 text-sm last:border-0"><input type="checkbox" checked={selected.includes(repository.repository_id)} onChange={(event) => toggle(repository.repository_id, event.target.checked)} disabled={repository.archived} />{repository.full_name}<span className="ml-auto text-xs text-muted-foreground">{repository.visibility}</span></label>)}</div> : null}</div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!accountId || (scope === 'selected' && !selected.length)} onClick={() => void create()}>{t('authorize')}</Button></DialogFooter></DialogContent></Dialog>;
}

function ConnectorDialog({ open, onOpenChange, workspaceId, kinds, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; kinds: Array<{ kind: string }>; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace'); const tc = useTranslations('common');
  const [name, setName] = useState(''); const [kind, setKind] = useState(''); const [endpoint, setEndpoint] = useState(''); const [authentication, setAuthentication] = useState('bearer_token'); const [credential, setCredential] = useState(''); const [credentialUsername, setCredentialUsername] = useState(''); const [verificationPath, setVerificationPath] = useState('/health'); const [tenantId, setTenantId] = useState(''); const [scopeValue, setScopeValue] = useState(''); const [scopeKey, setScopeKey] = useState('cluster'); const [host, setHost] = useState(''); const [port, setPort] = useState(''); const [database, setDatabase] = useState(''); const [databaseUsername, setDatabaseUsername] = useState(''); const [databasePassword, setDatabasePassword] = useState(''); const [certificate, setCertificate] = useState(''); const [table, setTable] = useState(''); const [timeColumn, setTimeColumn] = useState(''); const [stableOrder, setStableOrder] = useState('');
  const isEndpointConnector = ['loki', 'elasticsearch', 'opensearch', 'https'].includes(kind);
  const isDatabaseConnector = kind === 'postgresql' || kind === 'mysql';
  async function create() {
    try {
      await createConnector(workspaceId, {
        name,
        kind,
        ...(isEndpointConnector ? { endpoint } : {}),
        ...(kind === 'loki' ? { tenant_id: tenantId || undefined, root_matchers: [{ name: scopeKey, value: scopeValue }], authentication, credential } : {}),
        ...(kind === 'elasticsearch' || kind === 'opensearch' ? { authentication, credential, credential_username: credentialUsername || undefined, allowed_indices: splitValues(scopeValue) } : {}),
        ...(kind === 'https' ? { authentication, credential, credential_username: credentialUsername || undefined, verification_path: verificationPath, safe_read_path: scopeValue } : {}),
        ...(isDatabaseConnector ? { host, port: port ? Number(port) : undefined, database, database_username: databaseUsername, database_password: databasePassword, ca_certificate_pem: certificate, allowed_tables: [{ table, time_column: timeColumn, stable_order: splitValues(stableOrder) }] } : {}),
      });
      onOpenChange(false); await onCreated();
    } catch (cause) { toast.error(String(cause)); }
  }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[90vh] overflow-y-auto"><DialogHeader><DialogTitle>{t('addEvidenceConnector')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('name')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span className="field-label">{t('connectorKind')}</span><Select value={kind} onChange={(event) => { setKind(event.target.value); setCredential(''); setScopeValue(''); }}><option value="">{t('connectorKind')}</option>{kinds.map((item) => <option key={item.kind} value={item.kind}>{item.kind}</option>)}</Select></label>{isEndpointConnector ? <label className="field"><span className="field-label">{t('endpoint')}</span><Input placeholder="https://service.example.com" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label> : null}{kind === 'loki' ? <><label className="field"><span className="field-label">{t('tenantId')}</span><Input value={tenantId} onChange={(event) => setTenantId(event.target.value)} /></label><div className="grid gap-3 sm:grid-cols-2"><label className="field"><span className="field-label">{t('labelName')}</span><Input value={scopeKey} onChange={(event) => setScopeKey(event.target.value)} /></label><label className="field"><span className="field-label">{t('labelValue')}</span><Input value={scopeValue} onChange={(event) => setScopeValue(event.target.value)} /></label></div></> : null}{kind === 'elasticsearch' || kind === 'opensearch' ? <label className="field"><span className="field-label">{t('allowedIndices')}</span><Input placeholder="logs-production, logs-errors" value={scopeValue} onChange={(event) => setScopeValue(event.target.value)} /></label> : null}{kind === 'https' ? <><label className="field"><span className="field-label">{t('verificationPath')}</span><Input value={verificationPath} onChange={(event) => setVerificationPath(event.target.value)} /></label><label className="field"><span className="field-label">{t('safeReadPath')}</span><Input placeholder="/v1/events" value={scopeValue} onChange={(event) => setScopeValue(event.target.value)} /></label></> : null}{isEndpointConnector ? <><label className="field"><span className="field-label">{t('authentication')}</span><Select value={authentication} onChange={(event) => setAuthentication(event.target.value)}>{kind === 'loki' ? <option value="none">{t('none')}</option> : null}<option value="bearer_token">{t('bearerToken')}</option>{kind !== 'loki' ? <><option value="api_key">{t('apiKey')}</option><option value="basic">{t('basicAuth')}</option></> : null}</Select></label>{authentication === 'basic' ? <label className="field"><span className="field-label">{t('username')}</span><Input value={credentialUsername} onChange={(event) => setCredentialUsername(event.target.value)} /></label> : null}{authentication !== 'none' ? <label className="field"><span className="field-label">{t('credential')}</span><Input type="password" value={credential} onChange={(event) => setCredential(event.target.value)} /></label> : null}</> : null}{isDatabaseConnector ? <><p className="text-sm text-muted-foreground">{t('databaseConnectorConfiguration')}</p><div className="grid gap-3 sm:grid-cols-2"><label className="field"><span className="field-label">{t('databaseHost')}</span><Input value={host} onChange={(event) => setHost(event.target.value)} /></label><label className="field"><span className="field-label">{t('databasePort')}</span><Input inputMode="numeric" value={port} onChange={(event) => setPort(event.target.value)} /></label><label className="field"><span className="field-label">{t('databaseName')}</span><Input value={database} onChange={(event) => setDatabase(event.target.value)} /></label><label className="field"><span className="field-label">{t('username')}</span><Input value={databaseUsername} onChange={(event) => setDatabaseUsername(event.target.value)} /></label></div><label className="field"><span className="field-label">{t('password')}</span><Input type="password" value={databasePassword} onChange={(event) => setDatabasePassword(event.target.value)} /></label><label className="field"><span className="field-label">{t('caCertificate')}</span><Textarea className="min-h-24 font-mono" value={certificate} onChange={(event) => setCertificate(event.target.value)} /></label><div className="grid gap-3 sm:grid-cols-3"><label className="field"><span className="field-label">{t('allowedTable')}</span><Input placeholder="public.orders" value={table} onChange={(event) => setTable(event.target.value)} /></label><label className="field"><span className="field-label">{t('timeColumn')}</span><Input placeholder="created_at" value={timeColumn} onChange={(event) => setTimeColumn(event.target.value)} /></label><label className="field"><span className="field-label">{t('stableOrder')}</span><Input placeholder="id" value={stableOrder} onChange={(event) => setStableOrder(event.target.value)} /></label></div></> : null}</div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!name || !kind || (isEndpointConnector && (!endpoint || (kind === 'loki' && !scopeValue) || (kind !== 'loki' && !scopeValue) || (authentication !== 'none' && !credential))) || (isDatabaseConnector && (!host || !database || !databaseUsername || !databasePassword || !certificate || !table || !timeColumn || !stableOrder))} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}

function splitValues(value: string): string[] { return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean); }
