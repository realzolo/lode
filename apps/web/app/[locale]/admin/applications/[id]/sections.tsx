'use client';

// Per-application Settings tabs.
//
// Each Section renders either a read-only list (regular users) or a list +
// admin controls: a "+ Add" button that opens a Radix dialog, per-row
// remove actions gated behind a `<ConfirmDialog>`. After every successful
// mutation the Section calls `onRefresh()` to bump the parent
// `ApplicationLoader`'s fetch nonce, which re-runs `fetchApplication` and
// re-renders every Section in the same render pass — keeping the
// "概览/仓库/描述/数据源/模型" counters in the overview and the data
// inside each tab in lockstep without any per-tab polling.
//
// Kafka topic editing lives in the overview page (it's a single field there),
// so this file does not own it.

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select } from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  bindRepo,
  createDbSource,
  createApplicationDescription,
  createApplicationIntegration,
  createLocalRepo,
  deleteDbSource,
  deleteApplicationDescription,
  deleteApplicationIntegration,
  fetchAiModelConfigs,
  fetchApplication,
  getApplicationIntegration,
  fetchSettings,
  setApplicationModel,
  testDbSource,
  testApplicationIntegration,
  unbindRepo,
  updateDbSource,
  updateApplicationIntegration,
  type CreateApplicationDescriptionInput,
  type ApplicationIntegrationInput,
  type CreateDbSourceInput,
  type CreateLocalRepoInput,
  type UpdateDbSourceInput,
} from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { IconPlus, IconTrash2 } from '@/components/icons';

// The detail endpoint's repos/descriptions/db_sources dictionaries all carry `id`
// (and `repo_id` for repos): see `GET /applications/{id}` in
// `routes/applications.py`. The api client exposes them in
// `fetchApplication`'s return type too.
//
// The two short aliases below refine the inferred element types so each
// section can name them without `as unknown as` casts.

export type AppDetail = Awaited<ReturnType<typeof fetchApplication>>;

type BoundRepo = AppDetail['repos'][number];
type BoundDescription = AppDetail['descriptions'][number];
type BoundDbSource = AppDetail['db_sources'][number];
type BoundIntegration = AppDetail['integrations'][number];
type GlobalRepo = { id: number; name: string; url: string };
type CredentialOption = { id: number; label: string };

export function ServiceIntegrationsSection({
  data,
  appId,
  onRefresh,
}: {
  data: AppDetail;
  appId: string;
  onRefresh: () => void;
}) {
  const isAdmin = useUser().isAdmin;
  const tc = useTranslations('common');
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<BoundIntegration | null>(null);
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'redis' | 'kafka' | 'clickhouse'>('redis');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('');
  const [username, setUsername] = useState('');
  const [database, setDatabase] = useState('0');
  const [bootstrap, setBootstrap] = useState('');
  const [topics, setTopics] = useState('');
  const [secret, setSecret] = useState('');
  const [remove, setRemove] = useState<BoundIntegration | null>(null);
  const [testPassed, setTestPassed] = useState(false);
  const tAdmin = useTranslations('admin');

  function reset() {
    setEditing(null); setName(''); setKind('redis'); setHost(''); setPort('');
    setUsername(''); setDatabase('0'); setBootstrap(''); setTopics(''); setSecret('');
    setTestPassed(false);
  }

  function payload() {
    const config = kind === 'kafka'
      ? { bootstrap_servers: bootstrap.split(',').map((item) => item.trim()).filter(Boolean), username: username.trim(), topics: topics.split(',').map((item) => item.trim()).filter(Boolean) }
      : kind === 'redis'
        ? { host: host.trim(), port: Number(port || 6380), username: username.trim() || undefined, database: Number(database || 0), tls: true }
        : { host: host.trim(), port: Number(port || 8443), username: username.trim(), database: database.trim() || 'default', tls: true };
    return { name: name.trim(), kind, config, secret_ref: secret.trim() };
  }

  async function edit(integration: BoundIntegration) {
    setBusy(true); setError(null);
    try {
      const full = await getApplicationIntegration(appId, integration.id);
      const config = full.config;
      setEditing(integration); setName(full.name); setKind(full.kind);
      setHost(typeof config.host === 'string' ? config.host : '');
      setPort(typeof config.port === 'number' ? String(config.port) : '');
      setUsername(typeof config.username === 'string' ? config.username : '');
      setDatabase(typeof config.database === 'number' || typeof config.database === 'string' ? String(config.database) : '');
      setBootstrap(Array.isArray(config.bootstrap_servers) ? config.bootstrap_servers.map(String).join(', ') : '');
      setTopics(Array.isArray(config.topics) ? config.topics.map(String).join(', ') : '');
      setSecret(''); setTestPassed(false); setOpen(true);
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  async function save(testOnly = false) {
    setBusy(true); setError(null);
    if (!testOnly) setTestPassed(false);
    try {
      const input = payload();
      if (testOnly) {
        if (!input.secret_ref) throw new Error('测试需要提供凭据。');
        const result = await testApplicationIntegration(appId, input);
        if (!result.ok) throw new Error(result.error ?? '连接验证失败');
        setTestPassed(true);
      } else if (editing) {
        const { kind: _kind, ...update }: Partial<ApplicationIntegrationInput> = input;
        if (!update.secret_ref) delete update.secret_ref;
        await updateApplicationIntegration(appId, editing.id, update);
        setOpen(false); reset(); onRefresh();
      } else {
        if (!input.secret_ref) throw new Error('需要提供凭据或 env:// 引用。');
        await createApplicationIntegration(appId, input);
        setOpen(false); reset(); onRefresh();
      }
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  async function confirmRemove() {
    if (!remove) return;
    setBusy(true);
    try { await deleteApplicationIntegration(appId, remove.id); setRemove(null); onRefresh(); }
    catch (cause) { setError(String(cause)); throw cause; }
    finally { setBusy(false); }
  }

  return <Card>
    <div className="row-between"><div><h2 className="section-title">只读服务集成</h2><p className="muted">状态采集仅使用固定只读操作，连接端点需在 worker 出口白名单中。</p></div>
      {isAdmin && <Button size="sm" variant="primary" onClick={() => { reset(); setOpen(true); }}><IconPlus size={14} /> 添加集成</Button>}
    </div>
    {error && <p className="muted" role="alert" style={{ color: 'var(--danger)', marginTop: 10 }}>{error}</p>}
    <div className="stack" style={{ marginTop: 14 }}>
      {data.integrations.length === 0 ? <p className="muted">{tc('empty')}</p> : data.integrations.map((integration: BoundIntegration) => <div key={integration.id} className="row-between">
        <div><b>{integration.name}</b> <span className="muted mono">{integration.kind}</span><p className="muted" style={{ fontSize: 12 }}>{integration.last_collected_at ? `上次采集 ${integration.last_collected_at}` : '尚未采集'}</p></div>
        <div className="row" style={{ gap: 8 }}><span className="muted" style={{ color: integration.state === 'disabled' ? 'var(--danger)' : undefined }}>{integration.state === 'disabled' ? integration.last_error || '策略停用' : '已启用'}</span>
          {isAdmin && <><Button size="sm" variant="ghost" onClick={() => void edit(integration)}>编辑</Button><Button size="sm" variant="destructive" onClick={() => setRemove(integration)}><IconTrash2 size={14} /> {tc('delete')}</Button></>}
        </div>
      </div>)}
    </div>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>{editing ? '编辑服务集成' : '添加服务集成'}</DialogTitle><DialogDescription>秘密仅在提交时使用，永不回显。</DialogDescription></DialogHeader>
      <div className="stack"><Input placeholder="名称" value={name} onChange={(e) => { setName(e.target.value); setTestPassed(false); }} disabled={busy} />
        <Select value={kind} disabled={busy || !!editing} onChange={(e) => { setKind(e.target.value as typeof kind); setTestPassed(false); }}><option value="redis">Redis</option><option value="kafka">Kafka</option><option value="clickhouse">ClickHouse</option></Select>
        {kind === 'kafka' ? <><Input placeholder="broker.example.com:9093" value={bootstrap} onChange={(e) => { setBootstrap(e.target.value); setTestPassed(false); }} disabled={busy}/><Input placeholder="用户名" value={username} onChange={(e) => { setUsername(e.target.value); setTestPassed(false); }} disabled={busy}/><Input placeholder="主题（逗号分隔，可选）" value={topics} onChange={(e) => { setTopics(e.target.value); setTestPassed(false); }} disabled={busy}/></> : <><Input placeholder="DNS 主机名" value={host} onChange={(e) => { setHost(e.target.value); setTestPassed(false); }} disabled={busy}/><Input placeholder="端口" value={port} onChange={(e) => { setPort(e.target.value); setTestPassed(false); }} disabled={busy}/><Input placeholder="用户名（Redis 可选）" value={username} onChange={(e) => { setUsername(e.target.value); setTestPassed(false); }} disabled={busy}/><Input placeholder={kind === 'redis' ? '数据库编号' : '数据库'} value={database} onChange={(e) => { setDatabase(e.target.value); setTestPassed(false); }} disabled={busy}/></>}
        <Input type="password" placeholder={editing ? '凭据（留空以保持不变）' : '凭据或 env:// 引用'} value={secret} onChange={(e) => { setSecret(e.target.value); setTestPassed(false); }} disabled={busy}/>
        {testPassed && <p className="muted" role="status" style={{ color: 'var(--success)', fontSize: 13 }}>{tAdmin('integrationTestPassed')}</p>}
      </div><DialogFooter><Button variant="ghost" onClick={() => void save(true)} disabled={busy}>测试</Button><Button onClick={() => setOpen(false)} disabled={busy}>{tc('cancel')}</Button><Button variant="primary" onClick={() => void save()} disabled={busy || !name.trim()}>{tc('save')}</Button></DialogFooter>
    </DialogContent></Dialog>
    <ConfirmDialog open={!!remove} onOpenChange={(value) => !value && setRemove(null)} title="删除服务集成" description="将删除连接配置和凭据。" confirmLabel={tc('delete')} destructive onConfirm={confirmRemove} />
  </Card>;
}

// ---------------------------------------------------------------------------
// Application-scoped repos
// ---------------------------------------------------------------------------

export function ReposSection({
  data,
  appId,
  onRefresh,
}: {
  data: AppDetail;
  appId: string;
  onRefresh: () => void;
}) {
  const t = useTranslations('application');
  const tc = useTranslations('common');
  const tAdmin = useTranslations('admin');
  const isAdmin = useUser().isAdmin;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [repos, setRepos] = useState<GlobalRepo[]>([]);
  const [credentials, setCredentials] = useState<CredentialOption[]>([]);
  const [mode, setMode] = useState<'global' | 'local'>('global');
  const [selectedRepoId, setSelectedRepoId] = useState<string>('');
  const [description, setDescription] = useState('');
  const [localName, setLocalName] = useState('');
  const [localUrl, setLocalUrl] = useState('');
  const [localBranch, setLocalBranch] = useState('main');
  const [localType, setLocalType] = useState('github');
  const [localCredentialId, setLocalCredentialId] = useState('');
  const [unbind, setUnbind] = useState<BoundRepo | null>(null);

  const boundRepos = data.repos;
  // IDs already bound to this application — used to block re-binding (the
  // backend returns 409 on a duplicate (application_id, repo_id) binding).
  const boundRepoIds = useMemo(
    () => new Set(boundRepos.map((r) => r.repo_id)),
    [boundRepos],
  );

  useEffect(() => {
    if (!open || !isAdmin) return;
    let active = true;
    setError(null);
    fetchSettings()
      .then((s) => {
        if (!active) return;
        const gs: GlobalRepo[] = s.git_repos.map((r) => ({
          id: r.id,
          name: r.name,
          url: r.repo_url,
        }));
        setRepos(gs);
        setCredentials(
          s.git_credentials.map((c) => ({
            id: c.id,
            label: `${c.username || '—'} · ${c.auth_type}`,
          })),
        );
        const bound = new Set(boundRepos.map((r) => r.repo_id));
        const first = gs.find((r) => !bound.has(r.id));
        setSelectedRepoId(first ? String(first.id) : (gs[0] ? String(gs[0].id) : ''));
      })
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [open, isAdmin, boundRepos]);

  async function handleBind() {
    setBusy(true);
    setError(null);
    try {
      if (mode === 'global') {
        if (!selectedRepoId) return;
        await bindRepo(appId, {
          repo_id: Number(selectedRepoId),
          description,
        });
      } else {
        const payload: CreateLocalRepoInput = {
          name: localName.trim(),
          repo_url: localUrl.trim(),
          default_branch: localBranch.trim() || 'main',
          repo_type: localType,
          credential_id: localCredentialId ? Number(localCredentialId) : null,
          description,
        };
        await createLocalRepo(appId, payload);
      }
      setOpen(false);
      setDescription('');
      setLocalName('');
      setLocalUrl('');
      setLocalBranch('main');
      setLocalType('github');
      setLocalCredentialId('');
      onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleUnbind() {
    if (!unbind) return;
    setBusy(true);
    setError(null);
    try {
      await unbindRepo(appId, unbind.repo_id);
      setUnbind(null);
      onRefresh();
    } catch (e) {
      setError(String(e));
      throw e;
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <p className="page-subtitle">{t('reposDesc')}</p>
      {error && (
        <p className="muted" style={{ color: 'var(--danger)', fontSize: 13 }}>
          {error}
        </p>
      )}
      {isAdmin && (
        <div style={{ marginTop: 12 }}>
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              setOpen(true);
              setError(null);
            }}
          >
            <IconPlus size={14} /> {tAdmin('bindRepo')}
          </Button>
        </div>
      )}
      {boundRepos.length === 0 ? (
        <p className="muted" style={{ marginTop: 12 }}>{tc('empty')}</p>
      ) : (
        <div className="stack" style={{ marginTop: 12 }}>
          {boundRepos.map((r) => (
            <div key={r.repo_id} className="stack" style={{ gap: 4 }}>
              <div className="row-between">
                <span className="mono">{r.name}</span>
                <span className="muted" style={{ fontSize: 12 }}>{r.url}</span>
              </div>
              <p className="muted" style={{ fontSize: 13 }}>{r.description || '—'}</p>
              {isAdmin && (
                <div>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setUnbind(r)}
                  >
                    <IconTrash2 size={14} /> {tc('delete')}
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{tAdmin('bindRepo')}</DialogTitle>
            <DialogDescription>{tAdmin('bindRepoDesc')}</DialogDescription>
          </DialogHeader>
          <div className="stack">
            <Select value={mode} onChange={(e) => setMode(e.target.value as 'global' | 'local')} disabled={busy}>
              <option value="global">{tAdmin('repoModeGlobal')}</option>
              <option value="local">{tAdmin('repoModeLocal')}</option>
            </Select>
            {mode === 'global' ? (
              <Select
                value={selectedRepoId}
                onChange={(e) => setSelectedRepoId(e.target.value)}
                disabled={busy || repos.length === 0}
              >
                {repos.length === 0 ? (
                  <option value="">— no repos in registry —</option>
                ) : (
                  repos.map((r) => {
                    const isBound = boundRepoIds.has(r.id);
                    return (
                      <option key={r.id} value={String(r.id)} disabled={isBound}>
                        {r.name} ({r.url}){isBound ? ' — 已绑定' : ''}
                      </option>
                    );
                  })
                )}
              </Select>
            ) : (
              <>
                <Input
                  placeholder={tAdmin('repoName')}
                  value={localName}
                  onChange={(e) => setLocalName(e.target.value)}
                  disabled={busy}
                />
                <Input
                  placeholder={tAdmin('repoUrl')}
                  value={localUrl}
                  onChange={(e) => setLocalUrl(e.target.value)}
                  disabled={busy}
                />
                <Input
                  placeholder={tAdmin('defaultBranch')}
                  value={localBranch}
                  onChange={(e) => setLocalBranch(e.target.value)}
                  disabled={busy}
                />
                <Select value={localType} onChange={(e) => setLocalType(e.target.value)} disabled={busy}>
                  {['github', 'gitlab', 'gitee', 'bitbucket', 'other'].map((rt) => (
                    <option key={rt} value={rt}>{rt}</option>
                  ))}
                </Select>
                <Select
                  value={localCredentialId}
                  onChange={(e) => setLocalCredentialId(e.target.value)}
                  disabled={busy}
                >
                  <option value="">{tAdmin('noCredential')}</option>
                  {credentials.map((c) => (
                    <option key={c.id} value={String(c.id)}>{c.label}</option>
                  ))}
                </Select>
              </>
            )}
            <Textarea
              placeholder={tAdmin('repoDescription')}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={busy}
            />
          </div>
          <DialogFooter>
            <Button onClick={() => setOpen(false)} disabled={busy}>{tc('cancel')}</Button>
            <Button
              variant="primary"
              onClick={handleBind}
              disabled={
                busy ||
                (mode === 'global' &&
                  (!selectedRepoId || boundRepoIds.has(Number(selectedRepoId)))) ||
                (mode === 'local' && (!localName.trim() || !localUrl.trim()))
              }
            >
              {tc('save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={unbind != null}
        onOpenChange={(o) => !o && setUnbind(null)}
        title={tAdmin('unbindRepoTitle')}
        description={unbind ? tAdmin('unbindRepoDesc', { name: unbind.name }) : ''}
        confirmLabel={tc('delete')}
        cancelLabel={tc('cancel')}
        destructive
        onConfirm={handleUnbind}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Application-scoped descriptions
// ---------------------------------------------------------------------------

export function DescriptionsSection({
  data,
  appId,
  onRefresh,
}: {
  data: AppDetail;
  appId: string;
  onRefresh: () => void;
}) {
  const t = useTranslations('application');
  const tc = useTranslations('common');
  const tAdmin = useTranslations('admin');
  const isAdmin = useUser().isAdmin;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [descriptionType, setDescriptionType] = useState<'deploy' | 'other'>('deploy');
  const [content, setContent] = useState('');
  const [deleteDescription, setDeleteDescription] = useState<BoundDescription | null>(null);

  const descriptions = data.descriptions;

  async function handleCreate() {
    setBusy(true);
    setError(null);
    const payload: CreateApplicationDescriptionInput = {
      description_type: descriptionType,
      content,
    };
    try {
      await createApplicationDescription(appId, payload);
      setOpen(false);
      setContent('');
      onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!deleteDescription) return;
    setBusy(true);
    setError(null);
    try {
      await deleteApplicationDescription(appId, deleteDescription.id);
      setDeleteDescription(null);
      onRefresh();
    } catch (e) {
      setError(String(e));
      throw e;
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      {error && (
        <p className="muted" style={{ color: 'var(--danger)', fontSize: 13 }}>
          {error}
        </p>
      )}
      {isAdmin && (
        <div style={{ marginBottom: 12 }}>
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              setOpen(true);
              setError(null);
              setDescriptionType('deploy');
              setContent('');
            }}
          >
            <IconPlus size={14} /> {tAdmin('addDescription')}
          </Button>
        </div>
      )}
      {descriptions.length === 0 ? (
        <p className="muted">{tc('empty')}</p>
      ) : (
        <div className="stack">
          {descriptions.map((d) => (
            <div key={d.id} className="stack" style={{ gap: 4 }}>
              <div className="row-between">
                <span className="field-label">{d.description_type}</span>
                {isAdmin && (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setDeleteDescription(d)}
                  >
                    <IconTrash2 size={14} /> {tc('delete')}
                  </Button>
                )}
              </div>
              <p style={{ whiteSpace: 'pre-wrap' }}>{d.content}</p>
            </div>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{tAdmin('addDescription')}</DialogTitle>
            <DialogDescription>{t('descriptions')}</DialogDescription>
          </DialogHeader>
          <div className="stack">
            <Select
              value={descriptionType}
              onChange={(e) => setDescriptionType(e.target.value as 'deploy' | 'other')}
              disabled={busy}
            >
              <option value="deploy">deploy</option>
              <option value="other">other</option>
            </Select>
            <Textarea
              placeholder={tAdmin('descriptionContent')}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              disabled={busy}
              rows={6}
            />
          </div>
          <DialogFooter>
            <Button onClick={() => setOpen(false)} disabled={busy}>{tc('cancel')}</Button>
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={busy || !content.trim()}
            >
              {tc('save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleteDescription != null}
        onOpenChange={(o) => !o && setDeleteDescription(null)}
        title={tAdmin('deleteDescriptionTitle')}
        description={tAdmin('deleteDescriptionDesc')}
        confirmLabel={tc('delete')}
        cancelLabel={tc('cancel')}
        destructive
        onConfirm={handleDelete}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Application-scoped data sources
// ---------------------------------------------------------------------------

export function DbSourcesSection({
  data,
  appId,
  onRefresh,
}: {
  data: AppDetail;
  appId: string;
  onRefresh: () => void;
}) {
  const t = useTranslations('application');
  const tc = useTranslations('common');
  const tAdmin = useTranslations('admin');
  const isAdmin = useUser().isAdmin;
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; latency_ms: number | null; error: string | null } | null>(null);
  const [name, setName] = useState('');
  const [sourceDescription, setSourceDescription] = useState('');
  const [connSecretRef, setConnSecretRef] = useState('');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('5432');
  const [database, setDatabase] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [sslmode, setSslmode] = useState('');
  const [allowedTablesRaw, setAllowedTablesRaw] = useState('');
  const [sensitiveColumnsRaw, setSensitiveColumnsRaw] = useState('');
  const [deleteSource, setDeleteSource] = useState<BoundDbSource | null>(null);

  const sources = data.db_sources;
  const integrations = data.integrations ?? [];

  function resetForm() {
    setEditingId(null);
    setName('');
    setSourceDescription('');
    setConnSecretRef('');
    setHost('');
    setPort('5432');
    setDatabase('');
    setUsername('');
    setPassword('');
    setSslmode('');
    setAllowedTablesRaw('');
    setSensitiveColumnsRaw('');
    setTestResult(null);
    setError(null);
  }

  function startEdit(s: BoundDbSource) {
    setEditingId(s.id);
    setName(s.name);
    setSourceDescription(s.description ?? '');
    setConnSecretRef(s.conn_secret_ref ?? '');
    setHost(s.host ?? '');
    setPort(s.port ? String(s.port) : '5432');
    setDatabase(s.database ?? '');
    setUsername(s.username ?? '');
    setPassword(''); // never echo; only set to overwrite
    setSslmode(s.sslmode ?? '');
    setAllowedTablesRaw((s.allowed_tables as unknown[]).join(', '));
    setSensitiveColumnsRaw((s.sensitive_columns as unknown[]).join(', '));
    setTestResult(null);
    setError(null);
    setOpen(true);
  }

  function buildInput(): CreateDbSourceInput {
    const allowed_tables = allowedTablesRaw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    const sensitive_columns = sensitiveColumnsRaw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    const input: CreateDbSourceInput = {
      name: name.trim(),
      description: sourceDescription.trim(),
      allowed_tables,
      sensitive_columns,
    };
    // Structured connection mode takes precedence when a host is supplied.
    if (host.trim()) {
      input.host = host.trim();
      const parsedPort = parseInt(port, 10);
      if (!Number.isNaN(parsedPort)) input.port = parsedPort;
      if (database.trim()) input.database = database.trim();
      if (username.trim()) input.username = username.trim();
      if (password) input.password = password;
      if (sslmode.trim()) input.sslmode = sslmode.trim();
    } else if (connSecretRef.trim()) {
      input.conn_secret_ref = connSecretRef.trim();
    }
    return input;
  }

  async function handleSubmit() {
    setBusy(true);
    setError(null);
    try {
      const input = buildInput();
      if (editingId != null) {
        const update: UpdateDbSourceInput = { ...input };
        // On edit, omit unchanged secret ref / password unless re-entered.
        if (!connSecretRef.trim() && !host.trim()) {
          delete update.conn_secret_ref;
        }
        if (!password) delete update.password;
        await updateDbSource(appId, editingId, update);
      } else {
        await createDbSource(appId, input);
      }
      setOpen(false);
      resetForm();
      onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleTest() {
    setBusy(true);
    setError(null);
    setTestResult(null);
    try {
      const res = await testDbSource(appId, buildInput());
      setTestResult(res);
    } catch (e) {
      setTestResult({ ok: false, latency_ms: null, error: String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!deleteSource) return;
    setBusy(true);
    setError(null);
    try {
      await deleteDbSource(appId, deleteSource.id);
      setDeleteSource(null);
      onRefresh();
    } catch (e) {
      setError(String(e));
      throw e;
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      {error && (
        <p className="muted" style={{ color: 'var(--danger)', fontSize: 13 }}>
          {error}
        </p>
      )}
      {isAdmin && (
        <div style={{ marginBottom: 12 }}>
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              setOpen(true);
              resetForm();
            }}
          >
            <IconPlus size={14} /> {tAdmin('addDbSource')}
          </Button>
        </div>
      )}
      {sources.length === 0 ? (
        <p className="muted">{tc('empty')}</p>
      ) : (
        <div className="stack">
          {sources.map((s) => (
            <div key={s.id} className="stack" style={{ gap: 4 }}>
              <div className="row-between">
                <span className="field-label">{s.name}</span>
                {isAdmin && (
                  <div className="row" style={{ gap: 6 }}>
                    <Button size="sm" variant="ghost" onClick={() => startEdit(s)}>
                      {tc('edit')}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => setDeleteSource(s)}
                    >
                      <IconTrash2 size={14} /> {tc('delete')}
                    </Button>
                  </div>
                )}
              </div>
              {s.description ? (
                <p className="muted" style={{ fontSize: 13 }}>{s.description}</p>
              ) : null}
              <p className="muted mono" style={{ fontSize: 13 }}>
                {s.host
                  ? `${s.username ? s.username + '@' : ''}${s.host}${
                      s.port ? ':' + s.port : ''
                    }/${s.database ?? ''}${s.sslmode ? `?sslmode=${s.sslmode}` : ''}`
                  : s.conn_secret_ref ?? '—'}
                {s.has_password ? ' • •••' : ''}
              </p>
              <p className="muted mono" style={{ fontSize: 13 }}>
                allowed_tables:{' '}
                {Array.isArray(s.allowed_tables)
                  ? (s.allowed_tables as unknown[]).join(', ')
                  : '—'}
                {(s.sensitive_columns as unknown[]).length > 0 ? (
                  <>
                    {'  ·  sensitive: '}
                    {(s.sensitive_columns as unknown[]).join(', ')}
                  </>
                ) : null}
              </p>
            </div>
          ))}
        </div>
      )}
      {integrations.length > 0 && (
        <div className="stack" style={{ marginTop: 20 }}>
          <p className="field-label">只读服务集成</p>
          {integrations.map((integration: BoundIntegration) => (
            <div key={integration.id} className="row-between">
              <span>{integration.name} <span className="muted mono">{integration.kind}</span></span>
              <span className="muted" style={{ color: integration.state === 'disabled' ? 'var(--danger)' : undefined }}>
                {integration.state === 'disabled' ? integration.last_error || '已因只读策略停用' : '已验证只读'}
              </span>
            </div>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingId != null ? tAdmin('editDbSource') : tAdmin('addDbSource')}
            </DialogTitle>
            <DialogDescription>{t('dbSources')}</DialogDescription>
          </DialogHeader>
          <div className="stack">
            <Input
              placeholder={tAdmin('dbSourceName')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={busy}
            />
            <Textarea
              placeholder={tAdmin('dbSourceDescription')}
              value={sourceDescription}
              onChange={(e) => setSourceDescription(e.target.value)}
              disabled={busy}
              rows={3}
            />
            <Input
              placeholder={tAdmin('dbHost')}
              value={host}
              onChange={(e) => setHost(e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder={tAdmin('dbPort')}
              value={port}
              onChange={(e) => setPort(e.target.value)}
              disabled={busy}
              inputMode="numeric"
            />
            <Input
              placeholder={tAdmin('dbDatabase')}
              value={database}
              onChange={(e) => setDatabase(e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder={tAdmin('dbUsername')}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={busy}
            />
            <Input
              type="password"
              placeholder={
                editingId != null ? tAdmin('dbPasswordLeaveBlank') : tAdmin('dbPassword')
              }
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder={tAdmin('dbSecretRef')}
              value={connSecretRef}
              onChange={(e) => setConnSecretRef(e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder={tAdmin('dbSslmode')}
              value={sslmode}
              onChange={(e) => setSslmode(e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder={tAdmin('dbAllowedTables')}
              value={allowedTablesRaw}
              onChange={(e) => setAllowedTablesRaw(e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder={tAdmin('dbSensitiveColumns')}
              value={sensitiveColumnsRaw}
              onChange={(e) => setSensitiveColumnsRaw(e.target.value)}
              disabled={busy}
            />
            {testResult && (
              <p
                className="muted mono"
                style={{
                  fontSize: 13,
                  color: testResult.ok ? 'var(--success)' : 'var(--danger)',
                }}
              >
                {testResult.ok
                  ? tAdmin('dbTestOk', { ms: String(testResult.latency_ms) })
                  : tAdmin('dbTestFail', { error: testResult.error ?? '' })}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => setOpen(false)} disabled={busy}>{tc('cancel')}</Button>
            <Button
              variant="ghost"
              onClick={handleTest}
              disabled={busy || (!host.trim() && !connSecretRef.trim())}
            >
              {tAdmin('dbTest')}
            </Button>
            <Button
              variant="primary"
              onClick={handleSubmit}
              disabled={
                busy ||
                !name.trim() ||
                (!host.trim() && !connSecretRef.trim())
              }
            >
              {tc('save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleteSource != null}
        onOpenChange={(o) => !o && setDeleteSource(null)}
        title={tAdmin('deleteDbSourceTitle')}
        description={
          deleteSource ? tAdmin('deleteDbSourceDesc', { name: deleteSource.name }) : ''
        }
        confirmLabel={tc('delete')}
        cancelLabel={tc('cancel')}
        destructive
        onConfirm={handleDelete}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// AI model selection
// ---------------------------------------------------------------------------

export function ModelSection({
  data,
  appId,
  onRefresh,
}: {
  data: AppDetail;
  appId: string;
  onRefresh: () => void;
}) {
  const t = useTranslations('application');
  const tc = useTranslations('common');
  const tAdmin = useTranslations('admin');
  const isAdmin = useUser().isAdmin;
  const [rows, setRows] = useState<Awaited<ReturnType<typeof fetchAiModelConfigs>>>([]);
  const [selectedId, setSelectedId] = useState<string>(data.model_config_id ? String(data.model_config_id) : '');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const all = await fetchAiModelConfigs();
      setRows(all);
    } catch (e) {
      // 403 for non-admin → silent empty state; everything else surfaces.
      if (String(e).includes('403')) setRows([]);
      else setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId]);

  useEffect(() => {
    setSelectedId(data.model_config_id ? String(data.model_config_id) : '');
  }, [data.model_config_id]);

  async function handleSave() {
    setBusy(true);
    setError(null);
    try {
      await setApplicationModel(appId, selectedId ? Number(selectedId) : null);
      onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <p className="page-subtitle">{t('modelSelection')}</p>
      {error && (
        <p className="muted" style={{ color: 'var(--danger)', fontSize: 13 }}>
          {error}
        </p>
      )}
      {!isAdmin ? (
        <p className="muted" style={{ fontSize: 13 }}>
          {tAdmin('modelSelectionReadOnly')}
        </p>
      ) : loading ? (
        <Skeleton className="h-10 w-full" />
      ) : rows.length === 0 ? (
        <p className="muted" style={{ fontSize: 13 }}>
          {tAdmin('noGlobalModels')}
        </p>
      ) : (
        <div className="stack">
          <Select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            disabled={busy}
          >
            <option value="">{tAdmin('useDefaultModel')}</option>
            {rows.map((m) => (
              <option key={m.id} value={String(m.id)}>
                #{m.id} {m.provider} · {m.model}{m.is_default ? ' · default' : ''}
              </option>
            ))}
          </Select>
          <div>
            <Button
              size="sm"
              variant="primary"
              onClick={handleSave}
              disabled={busy || selectedId === (data.model_config_id ? String(data.model_config_id) : '')}
            >
              {tc('save')}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Loader
// ---------------------------------------------------------------------------
//
// `refreshNonce` starts at 0; every successful mutation bumps it, which
// reruns the useEffect and re-pulls `fetchApplication`, re-rendering every
// child Section. Sections call `onRefresh()` after their own state has
// already been applied locally, so the user sees their change *before* the
// network round-trip completes — the refresh just reconciles any divergence.

function ApplicationLoading() {
  return (
    <div aria-busy="true">
      <h1 className="page-title">
        <Skeleton className="h-8 w-48" />
      </h1>
      <Card className="stack" style={{ marginTop: 16 }}>
        <Skeleton className="h-3.5 w-24" />
        <Skeleton className="h-10 w-full max-w-[360px]" />
      </Card>
      <div style={{ marginTop: 20 }}>
        <Skeleton className="h-8 w-72" />
        <Card className="stack" style={{ marginTop: 16 }}>
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-4 w-2/3" />
        </Card>
      </div>
    </div>
  );
}

export function ApplicationLoader({
  id,
  refreshNonce,
  children,
}: {
  id: string;
  /** Bump via `setRefreshNonce(n => n + 1)` after a successful mutation. */
  refreshNonce: number;
  children: (data: AppDetail) => ReactNode;
}) {
  const tc = useTranslations('common');
  const [data, setData] = useState<AppDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchApplication(id)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [id, refreshNonce, retryNonce]);

  if (loading) return <ApplicationLoading />;
  if (error) {
    return (
      <div className="dashboard-error" role="alert">
        <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>
        <Button variant="outline" size="sm" onClick={() => setRetryNonce((nonce) => nonce + 1)}>{tc('retry')}</Button>
      </div>
    );
  }
  if (!data) return <p className="muted">{tc('empty')}</p>;
  return <>{children(data)}</>;
}

// Helper: after a mutation in a Section, bump the parent's refresh nonce and
// broadcast so the `ModelSection` (which loads from a different endpoint) also
// re-pulls.
export function makeRefreshDispatcher(
  setRefreshNonce: React.Dispatch<React.SetStateAction<number>>
): () => void {
  return () => {
    setRefreshNonce((n) => n + 1);
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('app-detail-refresh'));
    }
  };
}
