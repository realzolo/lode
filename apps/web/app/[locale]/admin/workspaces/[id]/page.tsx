'use client';

import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import { Activity, Database, GitBranch, Plus, RefreshCw, ScanSearch } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Tabs } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { createConnector, createLocalRepository, createModelBinding, fetchCapabilities, fetchConnectorKinds, fetchConnectors, fetchInvestigationPolicy, fetchModelBindings, fetchProviderAccounts, fetchRepositories, fetchResourceView, fetchWorkspace, introspectConnector, publishModelPolicy, testConnector, updateInvestigationPolicy } from '@/lib/api';
import { Link } from '@/lib/navigation';
import type { EvidenceConnector, InvestigationPolicy, ModelBinding, ProviderAccount, ProviderAccountModel, RepositoryBinding, Workspace } from '@/lib/types';

const roles = ['planner', 'native_query', 'synthesizer', 'verifier', 'context_compactor'];
const resourceViews = ['build-units', 'components', 'resource-graph-revisions', 'resource-observations', 'identity-resolutions'];

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
  const [connectors, setConnectors] = useState<EvidenceConnector[]>([]);
  const [investigationPolicy, setInvestigationPolicy] = useState<InvestigationPolicy | null>(null);
  const [kinds, setKinds] = useState<Array<{ kind: string; language: string; capabilities: string[]; secret_fields: string[] }>>([]);
  const [capabilities, setCapabilities] = useState<{ models: number; repositories: number; healthy_connectors: number; gaps: string[] } | null>(null);
  const [resources, setResources] = useState<Record<string, Array<Record<string, unknown>>>>({});
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<'binding' | 'repository' | 'connector' | null>(null);
  const load = useCallback(async () => {
    try {
      const [ws, modelRows, accountRows, repoRows, connectorRows, kindRows, caps, policy] = await Promise.all([
        fetchWorkspace(params.id), fetchModelBindings(params.id), fetchProviderAccounts(), fetchRepositories(params.id), fetchConnectors(params.id), fetchConnectorKinds(), fetchCapabilities(params.id), fetchInvestigationPolicy(params.id),
      ]);
      setWorkspace(ws); setBindings(modelRows); setAccountModels(flattenAccountModels(accountRows)); setRepositories(repoRows); setConnectors(connectorRows); setKinds(kindRows); setCapabilities(caps); setInvestigationPolicy(policy); setError('');
    } catch (cause) { setError(String(cause)); }
  }, [params.id]);
  useEffect(() => { void load(); }, [load]);

  const tabs = useMemo(() => [
    { value: 'overview', label: t('overview'), content: <Overview workspace={workspace} capabilities={capabilities} policy={investigationPolicy} onPolicyChanged={setInvestigationPolicy} /> },
    { value: 'models', label: t('modelPolicy'), content: <Models workspaceId={params.id} bindings={bindings} accountModels={accountModels} onAdd={() => setDialog('binding')} onChanged={load} /> },
    { value: 'repositories', label: t('repositories'), content: <Repositories rows={repositories} onAdd={() => setDialog('repository')} /> },
    { value: 'connectors', label: t('connectors'), content: <Connectors workspaceId={params.id} rows={connectors} onAdd={() => setDialog('connector')} onChanged={load} /> },
    { value: 'resources', label: t('resources'), content: <Resources workspaceId={params.id} values={resources} setValues={setResources} /> },
  ], [bindings, capabilities, connectors, accountModels, load, params.id, repositories, resources, workspace]);

  return <main className="space-y-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><p className="mb-2 text-sm text-muted-foreground"><Link href="/admin" className="hover:text-link">{t('workspace')}</Link> / {params.id}</p><h1 className="page-title">{workspace?.name || t('workspace')}</h1><p className="page-subtitle mono">{workspace?.ingestion_topic}</p></div><Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button></header>{error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}<Tabs items={tabs} />
    <BindingDialog open={dialog === 'binding'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} accountModels={accountModels} onCreated={load} />
    <RepositoryDialog open={dialog === 'repository'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} onCreated={load} />
    <ConnectorDialog open={dialog === 'connector'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} kinds={kinds} onCreated={load} />
  </main>;
}

function Overview({ workspace, capabilities, policy, onPolicyChanged }: { workspace: Workspace | null; capabilities: { models: number; repositories: number; healthy_connectors: number; gaps: string[] } | null; policy: InvestigationPolicy | null; onPolicyChanged: (policy: InvestigationPolicy) => void }) {
  const t = useTranslations('workspace');
  const stats = [[t('modelBindings'), capabilities?.models ?? 0], [t('repositories'), capabilities?.repositories ?? 0], [t('healthyConnectors'), capabilities?.healthy_connectors ?? 0]];
  async function changeProfile(profile: InvestigationPolicy['profile']) {
    if (!workspace || profile === policy?.profile) return;
    try { onPolicyChanged(await updateInvestigationPolicy(workspace.id, profile)); }
    catch (cause) { toast.error(String(cause)); }
  }
  return <section className="space-y-5"><div className="grid gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-3">{stats.map(([label, value]) => <div key={label} className="bg-card p-5"><p className="text-xs text-muted-foreground">{label}</p><strong className="mt-2 block text-2xl">{value}</strong></div>)}</div><div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('investigationDepth')}</h2><div className="mt-3 max-w-sm"><Select value={policy?.profile || ''} onChange={(event) => void changeProfile(event.target.value as InvestigationPolicy['profile'])}><option value="fast">{t('fast')}</option><option value="balanced">{t('balanced')}</option><option value="deep">{t('deep')}</option></Select><p className="mt-2 text-xs text-muted-foreground">{t('depthHelp', { revision: policy?.revision || '-' })}</p></div></div><div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('ingestion')}</h2><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="text-muted-foreground">{t('state')}</dt><dd>{workspace?.ingestion_state}</dd></div><div><dt className="text-muted-foreground">{t('version')}</dt><dd>{workspace?.ingestion_version}</dd></div><div><dt className="text-muted-foreground">{t('startPosition')}</dt><dd>{workspace?.ingestion_start_position || t('notStarted')}</dd></div></dl></div>{capabilities?.gaps.length ? <div className="border-t pt-5"><h2 className="text-sm font-semibold">{t('capabilityGaps')}</h2><div className="mt-2 flex flex-wrap gap-2">{capabilities.gaps.map((gap) => <span key={gap} className="rounded-sm bg-warning/10 px-2 py-1 text-xs text-warning-deep">{gap}</span>)}</div></div> : null}</section>;
}

function Models({ workspaceId, bindings, accountModels, onAdd, onChanged }: { workspaceId: string; bindings: ModelBinding[]; accountModels: ProviderAccountModel[]; onAdd: () => void; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace');
  async function publish() {
    try {
      const active = bindings.filter((row) => row.state === 'active');
      await publishModelPolicy(workspaceId, {
        eligible_binding_ids: active.map((row) => row.id),
        role_policies: Object.fromEntries(roles.map((role) => [role, { execution_classes: ['latency_optimized', 'reasoning_optimized'] }])),
        verifier_policy: { required_for_confirmed: true },
        pinned_evidence_kinds: ['incident_input', 'counter_evidence'], compression_levels: ['extractive', 'semantic'], minimum_output_tokens: 1024, provider_safety_margin_tokens: 512,
      });
      await onChanged();
    } catch (cause) { toast.error(String(cause)); }
  }
  return <section className="space-y-4"><div className="flex justify-between"><p className="text-sm text-muted-foreground">{t('policyHelp')}</p><div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => void publish()} disabled={!bindings.length}>{t('publishPolicy')}</Button><Button size="sm" onClick={onAdd}><Plus size={15} />{t('addBinding')}</Button></div></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('accountModel')}</th><th>{t('roles')}</th><th>{t('classes')}</th><th>{t('budget')}</th><th>{t('revision')}</th></tr></thead><tbody>{bindings.map((row) => <tr key={row.id}><td>{accountModels.find((item) => item.id === row.provider_account_model_id)?.display_name || row.provider_account_model_id}</td><td>{row.allowed_roles.join(', ')}</td><td>{row.execution_classes.join(', ')}</td><td>{t('calls', { calls: row.max_calls })}</td><td>{row.revision}</td></tr>)}</tbody></table></div></div></section>;
}

function Repositories({ rows, onAdd }: { rows: RepositoryBinding[]; onAdd: () => void }) {
  const t = useTranslations('workspace');
  return <section className="space-y-4"><div className="flex justify-end"><Button size="sm" onClick={onAdd}><Plus size={15} />{t('addRepository')}</Button></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('remote')}</th><th>{t('role')}</th><th>{t('branch')}</th><th>{t('revision')}</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="font-medium"><GitBranch className="mr-2 inline" size={15} />{row.name}</td><td className="mono text-xs">{row.repo_url}</td><td>{row.role}</td><td>{row.default_branch}</td><td>{row.revision}</td></tr>)}</tbody></table></div></div></section>;
}

function Connectors({ workspaceId, rows, onAdd, onChanged }: { workspaceId: string; rows: EvidenceConnector[]; onAdd: () => void; onChanged: () => Promise<void> }) {
  const t = useTranslations('workspace');
  async function runAction(action: () => Promise<unknown>) {
    try { await action(); await onChanged(); }
    catch (cause) { toast.error(String(cause)); }
  }
  return <section className="space-y-4"><div className="flex justify-end"><Button size="sm" onClick={onAdd}><Plus size={15} />{t('addConnector')}</Button></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('kind')}</th><th>{t('capabilities')}</th><th>{t('verification')}</th><th>{t('secrets')}</th><th /></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="font-medium"><Database className="mr-2 inline" size={15} />{row.name}</td><td>{row.kind}</td><td>{row.capabilities.join(', ')}</td><td>{row.verification_status}</td><td>{row.configured_secret_fields.join(', ') || t('none')}</td><td><div className="flex justify-end"><Button size="icon" variant="ghost" title={t('verify')} aria-label={t('verify')} onClick={() => void runAction(() => testConnector(workspaceId, row.id))}><Activity size={15} /></Button><Button size="icon" variant="ghost" title={t('introspect')} aria-label={t('introspect')} disabled={row.verification_status !== 'healthy'} onClick={() => void runAction(() => introspectConnector(workspaceId, row.id))}><ScanSearch size={15} /></Button></div></td></tr>)}</tbody></table></div></div></section>;
}

function Resources({ workspaceId, values, setValues }: { workspaceId: string; values: Record<string, Array<Record<string, unknown>>>; setValues: Dispatch<SetStateAction<Record<string, Array<Record<string, unknown>>>>> }) {
  const [selected, setSelected] = useState(resourceViews[0]);
  async function load(value: string) { setSelected(value); if (!values[value]) setValues((rows) => ({ ...rows, [value]: [] })); try { const result = await fetchResourceView(workspaceId, value); setValues((rows) => ({ ...rows, [value]: result })); } catch { setValues((rows) => ({ ...rows, [value]: [] })); } }
  return <section><div className="flex flex-wrap gap-1">{resourceViews.map((value) => <Button key={value} size="sm" variant={selected === value ? 'default' : 'ghost'} onClick={() => void load(value)}>{value}</Button>)}</div><pre className="mt-4 max-h-[520px] overflow-auto rounded-md border bg-card p-4 text-xs">{JSON.stringify(values[selected] || [], null, 2)}</pre></section>;
}

function BindingDialog({ open, onOpenChange, workspaceId, accountModels, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; accountModels: ProviderAccountModel[]; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [accountModel, setAccountModel] = useState(''); const [selectedRoles, setRoles] = useState<string[]>(roles);
  async function create() { try { await createModelBinding(workspaceId, { provider_account_model_id: Number(accountModel), execution_classes: ['latency_optimized', 'reasoning_optimized'], allowed_roles: selectedRoles, priority: 0, max_calls: 16, max_cost_per_call: 5, timeout_ms: 60000, allowed_data_classes: ['masked_operational', 'source_code'], max_context_utilization: 0.8 }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>{t('addModelBinding')}</DialogTitle></DialogHeader><Select value={accountModel} onChange={(e) => setAccountModel(e.target.value)}><option value="">{t('selectAccountModel')}</option>{accountModels.filter((row) => row.state === 'active').map((row) => <option key={row.id} value={row.id}>{row.display_name}</option>)}</Select><fieldset className="grid gap-2 sm:grid-cols-2"><legend className="mb-2 text-sm font-medium">{t('allowedRoles')}</legend>{roles.map((role) => <label key={role} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selectedRoles.includes(role)} onChange={(e) => setRoles((current) => e.target.checked ? [...current, role] : current.filter((item) => item !== role))} />{role}</label>)}</fieldset><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!accountModel || !selectedRoles.length} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}

function RepositoryDialog({ open, onOpenChange, workspaceId, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [form, setForm] = useState({ name: '', repo_url: '', default_branch: 'main', role: 'runtime_source' });
  async function create() { try { await createLocalRepository(workspaceId, { ...form, repo_type: 'other', credential_id: null, priority: 0, description: '' }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>{t('addReadOnlyRepository')}</DialogTitle></DialogHeader><div className="space-y-3"><Input placeholder={t('name')} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /><Input className="mono" placeholder={t('httpsSshFileUrl')} value={form.repo_url} onChange={(e) => setForm({ ...form, repo_url: e.target.value })} /><div className="grid gap-3 sm:grid-cols-2"><Input placeholder={t('defaultBranch')} value={form.default_branch} onChange={(e) => setForm({ ...form, default_branch: e.target.value })} /><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option value="runtime_source">{t('runtimeSource')}</option><option value="shared_library">{t('sharedLibrary')}</option><option value="infrastructure">{t('infrastructure')}</option><option value="documentation">{t('documentation')}</option></Select></div></div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!form.name || !form.repo_url} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}

function ConnectorDialog({ open, onOpenChange, workspaceId, kinds, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; kinds: Array<{ kind: string; secret_fields: string[] }>; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [name, setName] = useState(''); const [kind, setKind] = useState(''); const [config, setConfig] = useState('{}'); const [scope, setScope] = useState('{}'); const [secrets, setSecrets] = useState<Record<string, string>>({});
  const selected = kinds.find((item) => item.kind === kind);
  async function create() { try { await createConnector(workspaceId, { name, kind, config: JSON.parse(config), secrets: Object.fromEntries(Object.entries(secrets).filter(([, value]) => value)), scope_config: JSON.parse(scope), schema_catalog: {}, execution_budget_policy: { timeout_ms: 5000, max_rows: 1000, max_output_bytes: 1000000 } }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>{t('addEvidenceConnector')}</DialogTitle></DialogHeader><div className="space-y-3"><Input placeholder={t('name')} value={name} onChange={(e) => setName(e.target.value)} /><Select value={kind} onChange={(e) => { setKind(e.target.value); setSecrets({}); }}><option value="">{t('connectorKind')}</option>{kinds.map((item) => <option key={item.kind} value={item.kind}>{item.kind}</option>)}</Select><label className="field"><span className="field-label">{t('providerConfig')}</span><Textarea className="mono min-h-24" value={config} onChange={(e) => setConfig(e.target.value)} /></label><label className="field"><span className="field-label">{t('readScope')}</span><Textarea className="mono min-h-24" value={scope} onChange={(e) => setScope(e.target.value)} /></label>{selected?.secret_fields.map((field) => <Input key={field} type="password" placeholder={field} value={secrets[field] || ''} onChange={(e) => setSecrets({ ...secrets, [field]: e.target.value })} />)}</div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!name || !kind} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}
