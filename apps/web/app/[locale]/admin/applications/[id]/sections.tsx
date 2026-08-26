'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { CheckCircle2, Database, MoreHorizontal, Plus, Search, Server, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import {
  bindRepo, createApplicationArchitectureContext, createApplicationIntegration,
  deleteApplicationArchitectureContext, deleteApplicationIntegration, fetchAiModelConfigs,
  fetchApplication, fetchIntegrationKinds, fetchSettings, getApplicationIntegration,
  setApplicationModel, testApplicationIntegration, unbindRepo,
  updateApplicationIntegration, type ApplicationIntegrationInput, type IntegrationKind,
} from '@/lib/api';

export type AppDetail = Awaited<ReturnType<typeof fetchApplication>>;
type Integration = AppDetail['integrations'][number];

function parseField(field: IntegrationKind['form'][number], value: string): unknown {
  if (field.input === 'number') return Number(value);
  if (field.input === 'string-list') return value.split(',').map((item) => item.trim()).filter(Boolean);
  return value.trim();
}

export function IntegrationsSection({ data, appId, onRefresh }: { data: AppDetail; appId: string; onRefresh: () => void }) {
  const canManage = data.my_perm === 'admin';
  const [kinds, setKinds] = useState<IntegrationKind[]>([]);
  const [filter, setFilter] = useState('');
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Integration | null>(null);
  const [kind, setKind] = useState('');
  const [name, setName] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [tested, setTested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remove, setRemove] = useState<Integration | null>(null);

  useEffect(() => {
    fetchIntegrationKinds().then((items) => {
      setKinds(items);
      setKind((current) => current || items[0]?.kind || '');
    }).catch((cause) => setError(String(cause)));
  }, []);

  const definition = kinds.find((item) => item.kind === kind);
  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return needle ? data.integrations.filter((item) => `${item.name} ${item.kind}`.toLowerCase().includes(needle)) : data.integrations;
  }, [data.integrations, filter]);

  function reset() {
    setEditing(null); setName(''); setValues({}); setTested(false); setError(null);
    setKind(kinds[0]?.kind || '');
  }

  function buildInput(): ApplicationIntegrationInput {
    if (!definition) throw new Error('未找到集成类型定义');
    const config: Record<string, unknown> = {};
    const secrets: Record<string, string> = {};
    for (const field of definition.form) {
      const raw = values[field.key] ?? '';
      if (!raw && !field.required) continue;
      if (!raw && field.required && !(editing && field.secret)) throw new Error(`${field.key} 为必填项`);
      if (!raw) continue;
      if (field.secret) secrets[field.key] = raw;
      else config[field.key] = parseField(field, raw);
    }
    return { name: name.trim(), kind, config, secrets };
  }

  async function startEdit(item: Integration) {
    setBusy(true); setError(null);
    try {
      const full = await getApplicationIntegration(appId, item.id);
      setEditing(item); setKind(full.kind); setName(full.name);
      setValues(Object.fromEntries(Object.entries(full.config).map(([key, value]) => [key, Array.isArray(value) ? value.join(', ') : String(value)])));
      setTested(false); setOpen(true);
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  async function test() {
    setBusy(true); setError(null);
    try {
      const input = buildInput();
      if (editing) {
        const full = await getApplicationIntegration(appId, editing.id);
        input.config = { ...full.config, ...input.config };
      }
      const result = await testApplicationIntegration(appId, input);
      if (!result.ok) throw new Error(result.error || '连接测试失败');
      setTested(true);
    } catch (cause) { setTested(false); setError(String(cause)); }
    finally { setBusy(false); }
  }

  async function save() {
    setBusy(true); setError(null);
    try {
      const input = buildInput();
      if (editing) {
        await updateApplicationIntegration(appId, editing.id, { name: input.name, config: input.config, ...(Object.keys(input.secrets).length ? { secrets: input.secrets } : {}) });
      } else {
        await createApplicationIntegration(appId, input);
      }
      setOpen(false); reset(); onRefresh();
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  return <div className="space-y-4">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="relative max-w-sm flex-1"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="搜索名称或类型" /></div>
      {canManage && <Button variant="primary" onClick={() => { reset(); setOpen(true); }}><Plus className="h-4 w-4" />添加集成</Button>}
    </div>
    {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
    <Card className="overflow-hidden p-0 shadow-none">
      <div className="hidden grid-cols-[minmax(0,1.4fr)_minmax(120px,.7fr)_minmax(120px,.7fr)_44px] gap-3 border-b px-4 py-2 text-xs font-medium text-muted-foreground md:grid"><span>实例</span><span>能力</span><span>状态</span><span /></div>
      {visible.length === 0 ? <p className="px-4 py-10 text-center text-sm text-muted-foreground">暂无集成服务</p> : visible.map((item) => {
        const meta = kinds.find((entry) => entry.kind === item.kind);
        const capabilities = (meta?.capabilities || []).filter((value) => value !== 'test');
        const status = item.state === 'disabled' ? '已停用' : item.verification_status === 'verified' ? '已验证' : '验证失败';
        const statusVariant = item.state === 'active' && item.verification_status === 'verified' ? 'success' : 'warning';
        return <div key={item.id} className="grid min-h-16 grid-cols-[minmax(0,1fr)_44px] items-center gap-3 border-b px-4 py-3 last:border-b-0 md:grid-cols-[minmax(0,1.4fr)_minmax(120px,.7fr)_minmax(120px,.7fr)_44px]">
          <div className="flex min-w-0 items-start gap-3 md:items-center"><div className="flex h-8 w-8 shrink-0 items-center justify-center rounded border bg-muted">{item.kind === 'database' ? <Database className="h-4 w-4" /> : <Server className="h-4 w-4" />}</div><div className="min-w-0"><p className="truncate text-sm font-medium">{item.name}</p><p className="truncate text-xs text-muted-foreground">{meta?.label || item.kind} · r{item.revision}</p><div className="mt-2 flex flex-wrap gap-1 md:hidden">{capabilities.map((value) => <Badge key={value} variant="default">{value}</Badge>)}<Badge variant={statusVariant}>{status}</Badge></div></div></div>
          <div className="hidden flex-wrap gap-1 md:flex">{capabilities.map((value) => <Badge key={value} variant="default">{value}</Badge>)}</div>
          <div className="hidden md:block"><Badge variant={statusVariant}>{status}</Badge></div>
          {canManage ? <Button variant="ghost" size="icon" title="编辑集成" onClick={() => void startEdit(item)}><MoreHorizontal className="h-4 w-4" /></Button> : <span />}
        </div>;
      })}
    </Card>
    <Dialog open={open} onOpenChange={(value) => { setOpen(value); if (!value) reset(); }}><DialogContent><DialogHeader><DialogTitle>{editing ? '编辑集成' : '添加集成'}</DialogTitle><DialogDescription>连接信息由类型注册表定义；秘密只写入，不回显。</DialogDescription></DialogHeader>
      <div className="space-y-3"><Input value={name} onChange={(event) => { setName(event.target.value); setTested(false); }} placeholder="实例名称" disabled={busy} /><Select value={kind} onChange={(event) => { setKind(event.target.value); setValues({}); setTested(false); }} disabled={busy || Boolean(editing)}>{kinds.map((item) => <option key={item.kind} value={item.kind}>{item.label}</option>)}</Select>
        {definition?.form.map((field) => field.input === 'select' ? <Select key={field.key} value={values[field.key] || ''} onChange={(event) => { setValues((old) => ({ ...old, [field.key]: event.target.value })); setTested(false); }} disabled={busy}><option value="">{field.key}</option>{field.options?.map((option) => <option key={option} value={option}>{option}</option>)}</Select> : <Input key={field.key} type={field.secret ? 'password' : field.input === 'number' ? 'number' : 'text'} value={values[field.key] || ''} onChange={(event) => { setValues((old) => ({ ...old, [field.key]: event.target.value })); setTested(false); }} placeholder={`${field.key}${editing && field.secret ? '（留空保持不变）' : field.required ? ' *' : ''}`} disabled={busy} />)}
        {tested && <p className="flex items-center gap-2 text-sm text-[var(--success)]"><CheckCircle2 className="h-4 w-4" />连接验证通过</p>}
      </div><DialogFooter><Button variant="ghost" onClick={() => setRemove(editing)} disabled={!editing || busy}><Trash2 className="h-4 w-4" />删除</Button><Button variant="outline" onClick={() => void test()} disabled={busy || !name.trim()}>测试连接</Button><Button variant="primary" onClick={() => void save()} disabled={busy || !name.trim()}>保存</Button></DialogFooter>
    </DialogContent></Dialog>
    <ConfirmDialog open={Boolean(remove)} onOpenChange={(value) => !value && setRemove(null)} title="删除集成" description={remove ? `删除 ${remove.name} 及其加密凭据。` : ''} confirmLabel="删除" destructive onConfirm={async () => { if (!remove) return; await deleteApplicationIntegration(appId, remove.id); setRemove(null); setOpen(false); onRefresh(); }} />
  </div>;
}

export function ReposSection({ data, appId, onRefresh }: { data: AppDetail; appId: string; onRefresh: () => void }) {
  const canManage = data.my_perm === 'admin';
  const [available, setAvailable] = useState<{ id: number; name: string; repo_url: string }[]>([]);
  const [selected, setSelected] = useState(''); const [error, setError] = useState<string | null>(null);
  useEffect(() => { fetchSettings().then((value) => setAvailable(value.git_repos)).catch((cause) => setError(String(cause))); }, []);
  return <Card className="space-y-4"><div className="flex gap-2">{canManage && <><Select value={selected} onChange={(event) => setSelected(event.target.value)}><option value="">选择全局仓库</option>{available.filter((repo) => !data.repos.some((bound) => bound.repo_id === repo.id)).map((repo) => <option key={repo.id} value={repo.id}>{repo.name}</option>)}</Select><Button variant="primary" disabled={!selected} onClick={async () => { try { await bindRepo(appId, { repo_id: Number(selected), description: '' }); setSelected(''); onRefresh(); } catch (cause) { setError(String(cause)); } }}>绑定</Button></>}</div>{error && <p className="text-sm text-destructive">{error}</p>}{data.repos.map((repo) => <div key={repo.id} className="flex items-center justify-between border-b py-3 last:border-0"><div><p className="text-sm font-medium">{repo.name}</p><p className="text-xs text-muted-foreground">{repo.url}</p></div>{canManage && <Button variant="ghost" size="icon" title="解除绑定" onClick={async () => { await unbindRepo(appId, repo.repo_id); onRefresh(); }}><Trash2 className="h-4 w-4" /></Button>}</div>)}</Card>;
}

export function ArchitectureContextSection({ data, appId, onRefresh }: { data: AppDetail; appId: string; onRefresh: () => void }) {
  const canManage = data.my_perm === 'admin'; const [content, setContent] = useState(''); const [error, setError] = useState<string | null>(null);
  return <Card className="space-y-4">{canManage && <div className="space-y-2"><Textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="服务边界、关键调用链、部署拓扑或业务约束" /><Button variant="primary" disabled={!content.trim()} onClick={async () => { try { await createApplicationArchitectureContext(appId, { content }); setContent(''); onRefresh(); } catch (cause) { setError(String(cause)); } }}><Plus className="h-4 w-4" />添加上下文</Button></div>}{error && <p className="text-sm text-destructive">{error}</p>}{data.architecture_contexts.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">暂无架构上下文</p>}{data.architecture_contexts.map((item) => <div key={item.id} className="flex items-start justify-between gap-4 border-b py-3 last:border-0"><p className="whitespace-pre-wrap text-sm leading-6">{item.content}</p>{canManage && <Button variant="ghost" size="icon" title="删除上下文" onClick={async () => { await deleteApplicationArchitectureContext(appId, item.id); onRefresh(); }}><Trash2 className="h-4 w-4" /></Button>}</div>)}</Card>;
}

export function ModelSection({ data, appId, onRefresh }: { data: AppDetail; appId: string; onRefresh: () => void }) {
  const canManage = data.my_perm === 'admin'; const [models, setModels] = useState<Awaited<ReturnType<typeof fetchAiModelConfigs>>>([]); const [selected, setSelected] = useState(data.model_config_id ? String(data.model_config_id) : ''); const [error, setError] = useState<string | null>(null);
  useEffect(() => { fetchAiModelConfigs().then(setModels).catch((cause) => setError(String(cause))); }, []);
  return <Card className="space-y-3">{error && <p className="text-sm text-destructive">{error}</p>}<Select value={selected} onChange={(event) => setSelected(event.target.value)} disabled={!canManage}><option value="">选择模型</option>{models.map((model) => <option key={model.id} value={model.id}>{model.provider} · {model.model} · {model.last_test_status}</option>)}</Select>{canManage && <Button variant="primary" disabled={!selected} onClick={async () => { await setApplicationModel(appId, Number(selected)); onRefresh(); }}>保存</Button>}</Card>;
}

function ApplicationLoading() { return <div className="space-y-4" aria-busy="true"><Skeleton className="h-8 w-48" /><Card className="space-y-3"><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-5/6" /></Card></div>; }

export function ApplicationLoader({ id, refreshNonce, children }: { id: string; refreshNonce: number; children: (data: AppDetail) => ReactNode }) {
  const [data, setData] = useState<AppDetail | null>(null); const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(true);
  useEffect(() => { let active = true; setLoading(true); fetchApplication(id).then((value) => { if (active) { setData(value); setError(null); } }).catch((cause) => active && setError(String(cause))).finally(() => active && setLoading(false)); return () => { active = false; }; }, [id, refreshNonce]);
  if (loading) return <ApplicationLoading />; if (error) return <p className="text-sm text-destructive">{error}</p>; return data ? <>{children(data)}</> : null;
}

export function makeRefreshDispatcher(setRefreshNonce: React.Dispatch<React.SetStateAction<number>>): () => void { return () => setRefreshNonce((value) => value + 1); }
