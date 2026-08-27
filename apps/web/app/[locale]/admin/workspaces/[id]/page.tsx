'use client';

import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import { Activity, Database, GitBranch, Plus, RefreshCw, ScanSearch } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Tabs } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { createConnector, createLocalRepository, createModelBinding, fetchCapabilities, fetchConnectorKinds, fetchConnectors, fetchModelBindings, fetchModelDeployments, fetchRepositories, fetchResourceView, fetchWorkspace, introspectConnector, publishModelPolicy, testConnector } from '@/lib/api';
import { Link } from '@/lib/navigation';
import type { EvidenceConnector, ModelBinding, ModelDeployment, RepositoryBinding, Workspace } from '@/lib/types';

const roles = ['planner', 'native_query', 'synthesizer', 'verifier', 'context_compactor'];
const resourceViews = ['build-units', 'components', 'resource-graph-revisions', 'resource-observations', 'identity-resolutions'];

export default function WorkspacePage({ params }: { params: { id: string } }) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [bindings, setBindings] = useState<ModelBinding[]>([]);
  const [deployments, setDeployments] = useState<ModelDeployment[]>([]);
  const [repositories, setRepositories] = useState<RepositoryBinding[]>([]);
  const [connectors, setConnectors] = useState<EvidenceConnector[]>([]);
  const [kinds, setKinds] = useState<Array<{ kind: string; language: string; capabilities: string[]; secret_fields: string[] }>>([]);
  const [capabilities, setCapabilities] = useState<{ models: number; repositories: number; healthy_connectors: number; gaps: string[] } | null>(null);
  const [resources, setResources] = useState<Record<string, Array<Record<string, unknown>>>>({});
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<'binding' | 'repository' | 'connector' | null>(null);
  const load = useCallback(async () => {
    try {
      const [ws, modelRows, deploymentRows, repoRows, connectorRows, kindRows, caps] = await Promise.all([
        fetchWorkspace(params.id), fetchModelBindings(params.id), fetchModelDeployments(), fetchRepositories(params.id), fetchConnectors(params.id), fetchConnectorKinds(), fetchCapabilities(params.id),
      ]);
      setWorkspace(ws); setBindings(modelRows); setDeployments(deploymentRows); setRepositories(repoRows); setConnectors(connectorRows); setKinds(kindRows); setCapabilities(caps); setError('');
    } catch (cause) { setError(String(cause)); }
  }, [params.id]);
  useEffect(() => { void load(); }, [load]);

  const tabs = useMemo(() => [
    { value: 'overview', label: 'Overview', content: <Overview workspace={workspace} capabilities={capabilities} /> },
    { value: 'models', label: 'Model policy', content: <Models workspaceId={params.id} bindings={bindings} deployments={deployments} onAdd={() => setDialog('binding')} onChanged={load} /> },
    { value: 'repositories', label: 'Repositories', content: <Repositories rows={repositories} onAdd={() => setDialog('repository')} /> },
    { value: 'connectors', label: 'Connectors', content: <Connectors workspaceId={params.id} rows={connectors} onAdd={() => setDialog('connector')} onChanged={load} /> },
    { value: 'resources', label: 'Resources', content: <Resources workspaceId={params.id} values={resources} setValues={setResources} /> },
  ], [bindings, capabilities, connectors, deployments, load, params.id, repositories, resources, workspace]);

  return <main className="space-y-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><p className="mb-2 text-sm text-muted-foreground"><Link href="/admin" className="hover:text-link">Workspaces</Link> / {params.id}</p><h1 className="page-title">{workspace?.name || 'Workspace'}</h1><p className="page-subtitle mono">{workspace?.ingestion_topic}</p></div><Button size="icon" variant="outline" aria-label="Refresh" onClick={() => void load()}><RefreshCw size={16} /></Button></header>{error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}<Tabs items={tabs} />
    <BindingDialog open={dialog === 'binding'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} deployments={deployments} onCreated={load} />
    <RepositoryDialog open={dialog === 'repository'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} onCreated={load} />
    <ConnectorDialog open={dialog === 'connector'} onOpenChange={(value) => !value && setDialog(null)} workspaceId={params.id} kinds={kinds} onCreated={load} />
  </main>;
}

function Overview({ workspace, capabilities }: { workspace: Workspace | null; capabilities: { models: number; repositories: number; healthy_connectors: number; gaps: string[] } | null }) {
  const stats = [['Model bindings', capabilities?.models ?? 0], ['Repositories', capabilities?.repositories ?? 0], ['Healthy connectors', capabilities?.healthy_connectors ?? 0]];
  return <section className="space-y-5"><div className="grid gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-3">{stats.map(([label, value]) => <div key={label} className="bg-card p-5"><p className="text-xs text-muted-foreground">{label}</p><strong className="mt-2 block text-2xl">{value}</strong></div>)}</div><div className="border-t pt-5"><h2 className="text-sm font-semibold">Ingestion</h2><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="text-muted-foreground">State</dt><dd>{workspace?.ingestion_state}</dd></div><div><dt className="text-muted-foreground">Version</dt><dd>{workspace?.ingestion_version}</dd></div><div><dt className="text-muted-foreground">Start position</dt><dd>{workspace?.ingestion_start_position || 'Not started'}</dd></div></dl></div>{capabilities?.gaps.length ? <div className="border-t pt-5"><h2 className="text-sm font-semibold">Capability gaps</h2><div className="mt-2 flex flex-wrap gap-2">{capabilities.gaps.map((gap) => <span key={gap} className="rounded-sm bg-warning/10 px-2 py-1 text-xs text-warning-deep">{gap}</span>)}</div></div> : null}</section>;
}

function Models({ workspaceId, bindings, deployments, onAdd, onChanged }: { workspaceId: string; bindings: ModelBinding[]; deployments: ModelDeployment[]; onAdd: () => void; onChanged: () => Promise<void> }) {
  async function publish() {
    try {
      const active = bindings.filter((row) => row.state === 'active');
      await publishModelPolicy(workspaceId, {
        eligible_binding_ids: active.map((row) => row.id),
        role_policies: Object.fromEntries(roles.map((role) => [role, { execution_classes: ['latency_optimized', 'reasoning_optimized'] }])),
        budget_policy: { max_calls: 32, max_cost: 25 }, verifier_policy: { required_for_confirmed: true },
        pinned_evidence_kinds: ['incident_input', 'counter_evidence'], compression_levels: ['extractive', 'semantic'], minimum_output_tokens: 1024, provider_safety_margin_tokens: 512,
      });
      await onChanged();
    } catch (cause) { toast.error(String(cause)); }
  }
  return <section className="space-y-4"><div className="flex justify-between"><p className="text-sm text-muted-foreground">Every published policy freezes exact binding revisions.</p><div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => void publish()} disabled={!bindings.length}>Publish policy</Button><Button size="sm" onClick={onAdd}><Plus size={15} />Add binding</Button></div></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>Deployment</th><th>Roles</th><th>Classes</th><th>Budget</th><th>Revision</th></tr></thead><tbody>{bindings.map((row) => <tr key={row.id}><td>{deployments.find((item) => item.id === row.model_deployment_id)?.display_name || row.model_deployment_id}</td><td>{row.allowed_roles.join(', ')}</td><td>{row.execution_classes.join(', ')}</td><td>{row.max_calls} calls / {row.max_input_tokens.toLocaleString()} tokens</td><td>{row.revision}</td></tr>)}</tbody></table></div></div></section>;
}

function Repositories({ rows, onAdd }: { rows: RepositoryBinding[]; onAdd: () => void }) {
  return <section className="space-y-4"><div className="flex justify-end"><Button size="sm" onClick={onAdd}><Plus size={15} />Add repository</Button></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Remote</th><th>Role</th><th>Branch</th><th>Revision</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="font-medium"><GitBranch className="mr-2 inline" size={15} />{row.name}</td><td className="mono text-xs">{row.repo_url}</td><td>{row.role}</td><td>{row.default_branch}</td><td>{row.revision}</td></tr>)}</tbody></table></div></div></section>;
}

function Connectors({ workspaceId, rows, onAdd, onChanged }: { workspaceId: string; rows: EvidenceConnector[]; onAdd: () => void; onChanged: () => Promise<void> }) {
  async function runAction(action: () => Promise<unknown>) {
    try { await action(); await onChanged(); }
    catch (cause) { toast.error(String(cause)); }
  }
  return <section className="space-y-4"><div className="flex justify-end"><Button size="sm" onClick={onAdd}><Plus size={15} />Add connector</Button></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Kind</th><th>Capabilities</th><th>Verification</th><th>Secrets</th><th /></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="font-medium"><Database className="mr-2 inline" size={15} />{row.name}</td><td>{row.kind}</td><td>{row.capabilities.join(', ')}</td><td>{row.verification_status}</td><td>{row.configured_secret_fields.join(', ') || 'None'}</td><td><div className="flex justify-end"><Button size="icon" variant="ghost" title="Verify" onClick={() => void runAction(() => testConnector(workspaceId, row.id))}><Activity size={15} /></Button><Button size="icon" variant="ghost" title="Introspect" disabled={row.verification_status !== 'healthy'} onClick={() => void runAction(() => introspectConnector(workspaceId, row.id))}><ScanSearch size={15} /></Button></div></td></tr>)}</tbody></table></div></div></section>;
}

function Resources({ workspaceId, values, setValues }: { workspaceId: string; values: Record<string, Array<Record<string, unknown>>>; setValues: Dispatch<SetStateAction<Record<string, Array<Record<string, unknown>>>>> }) {
  const [selected, setSelected] = useState(resourceViews[0]);
  async function load(value: string) { setSelected(value); if (!values[value]) setValues((rows) => ({ ...rows, [value]: [] })); try { const result = await fetchResourceView(workspaceId, value); setValues((rows) => ({ ...rows, [value]: result })); } catch { setValues((rows) => ({ ...rows, [value]: [] })); } }
  return <section><div className="flex flex-wrap gap-1">{resourceViews.map((value) => <Button key={value} size="sm" variant={selected === value ? 'default' : 'ghost'} onClick={() => void load(value)}>{value}</Button>)}</div><pre className="mt-4 max-h-[520px] overflow-auto rounded-md border bg-card p-4 text-xs">{JSON.stringify(values[selected] || [], null, 2)}</pre></section>;
}

function BindingDialog({ open, onOpenChange, workspaceId, deployments, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; deployments: ModelDeployment[]; onCreated: () => Promise<void> }) {
  const [deployment, setDeployment] = useState(''); const [selectedRoles, setRoles] = useState<string[]>(roles);
  async function create() { try { await createModelBinding(workspaceId, { model_deployment_id: Number(deployment), execution_classes: ['latency_optimized', 'reasoning_optimized'], allowed_roles: selectedRoles, priority: 0, max_calls: 16, max_input_tokens: 64000, max_output_tokens: 4096, max_cost_per_call: 5, timeout_ms: 60000, allowed_data_classes: ['masked_operational', 'source_code'], max_context_utilization: 0.8 }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>Add model binding</DialogTitle></DialogHeader><Select value={deployment} onChange={(e) => setDeployment(e.target.value)}><option value="">Deployment</option>{deployments.filter((row) => row.state === 'active').map((row) => <option key={row.id} value={row.id}>{row.display_name}</option>)}</Select><fieldset className="grid gap-2 sm:grid-cols-2"><legend className="mb-2 text-sm font-medium">Allowed roles</legend>{roles.map((role) => <label key={role} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selectedRoles.includes(role)} onChange={(e) => setRoles((current) => e.target.checked ? [...current, role] : current.filter((item) => item !== role))} />{role}</label>)}</fieldset><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!deployment || !selectedRoles.length} onClick={() => void create()}>Create</Button></DialogFooter></DialogContent></Dialog>;
}

function RepositoryDialog({ open, onOpenChange, workspaceId, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; onCreated: () => Promise<void> }) {
  const [form, setForm] = useState({ name: '', repo_url: '', default_branch: 'main', role: 'runtime_source' });
  async function create() { try { await createLocalRepository(workspaceId, { ...form, repo_type: 'other', credential_id: null, priority: 0, description: '' }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>Add read-only repository</DialogTitle></DialogHeader><div className="space-y-3"><Input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /><Input className="mono" placeholder="HTTPS, SSH, or file URL" value={form.repo_url} onChange={(e) => setForm({ ...form, repo_url: e.target.value })} /><div className="grid gap-3 sm:grid-cols-2"><Input placeholder="Default branch" value={form.default_branch} onChange={(e) => setForm({ ...form, default_branch: e.target.value })} /><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option value="runtime_source">Runtime source</option><option value="shared_library">Shared library</option><option value="infrastructure">Infrastructure</option><option value="documentation">Documentation</option></Select></div></div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!form.name || !form.repo_url} onClick={() => void create()}>Create</Button></DialogFooter></DialogContent></Dialog>;
}

function ConnectorDialog({ open, onOpenChange, workspaceId, kinds, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; kinds: Array<{ kind: string; secret_fields: string[] }>; onCreated: () => Promise<void> }) {
  const [name, setName] = useState(''); const [kind, setKind] = useState(''); const [config, setConfig] = useState('{}'); const [scope, setScope] = useState('{}'); const [secrets, setSecrets] = useState<Record<string, string>>({});
  const selected = kinds.find((item) => item.kind === kind);
  async function create() { try { await createConnector(workspaceId, { name, kind, config: JSON.parse(config), secrets: Object.fromEntries(Object.entries(secrets).filter(([, value]) => value)), scope_config: JSON.parse(scope), schema_catalog: {}, execution_budget_policy: { timeout_ms: 5000, max_rows: 1000, max_output_bytes: 1000000 } }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>Add evidence connector</DialogTitle></DialogHeader><div className="space-y-3"><Input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} /><Select value={kind} onChange={(e) => { setKind(e.target.value); setSecrets({}); }}><option value="">Connector kind</option>{kinds.map((item) => <option key={item.kind} value={item.kind}>{item.kind}</option>)}</Select><label className="field"><span className="field-label">Provider config JSON</span><Textarea className="mono min-h-24" value={config} onChange={(e) => setConfig(e.target.value)} /></label><label className="field"><span className="field-label">Read scope JSON</span><Textarea className="mono min-h-24" value={scope} onChange={(e) => setScope(e.target.value)} /></label>{selected?.secret_fields.map((field) => <Input key={field} type="password" placeholder={field} value={secrets[field] || ''} onChange={(e) => setSecrets({ ...secrets, [field]: e.target.value })} />)}</div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!name || !kind} onClick={() => void create()}>Create</Button></DialogFooter></DialogContent></Dialog>;
}
