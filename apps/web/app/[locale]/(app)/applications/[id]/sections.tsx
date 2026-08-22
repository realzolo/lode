'use client';

// Per-application Settings tabs.
//
// Each Section renders either a read-only list (regular users) or a list +
// admin controls: a "+ Add" button that opens a Radix dialog, per-row
// remove actions gated behind a `<ConfirmDialog>`. After every successful
// mutation the Section calls `onRefresh()` to bump the parent
// `ApplicationLoader`'s fetch nonce, which re-runs `fetchApplication` and
// re-renders every Section in the same render pass — keeping the
// "概览/仓库/提示词/数据源/模型" counters in the overview and the data
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
  createPresetPrompt,
  deleteDbSource,
  deletePresetPrompt,
  fetchAiModelConfigs,
  fetchApplication,
  fetchSettings,
  unbindRepo,
  updateAiModel,
  type CreatePresetPromptInput,
} from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { IconPlus, IconTrash2 } from '@/components/icons';

// The detail endpoint's repos/prompts/db_sources dictionaries all carry `id`
// (and `repo_id` for repos): see `GET /applications/{id}` in
// `routes/applications.py`. The api client exposes them in
// `fetchApplication`'s return type too.
//
// The two short aliases below refine the inferred element types so each
// section can name them without `as unknown as` casts.

export type AppDetail = Awaited<ReturnType<typeof fetchApplication>>;

type BoundRepo = AppDetail['repos'][number];
type BoundPrompt = AppDetail['preset_prompts'][number];
type BoundDbSource = AppDetail['db_sources'][number];
type GlobalRepo = { id: number; name: string; url: string };

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
  const [selectedRepoId, setSelectedRepoId] = useState<string>('');
  const [description, setDescription] = useState('');
  const [unbind, setUnbind] = useState<BoundRepo | null>(null);

  const boundRepos = data.repos;

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
    if (!selectedRepoId) return;
    setBusy(true);
    setError(null);
    try {
      await bindRepo(appId, {
        repo_id: Number(selectedRepoId),
        description,
      });
      setOpen(false);
      setDescription('');
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
            <Select
              value={selectedRepoId}
              onChange={(e) => setSelectedRepoId(e.target.value)}
              disabled={busy || repos.length === 0}
            >
              {repos.length === 0 ? (
                <option value="">— no repos in registry —</option>
              ) : (
                repos.map((r) => (
                  <option key={r.id} value={String(r.id)}>
                    {r.name} ({r.url})
                  </option>
                ))
              )}
            </Select>
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
              disabled={busy || !selectedRepoId}
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
// Application-scoped preset prompts
// ---------------------------------------------------------------------------

export function PromptsSection({
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
  const [type, setType] = useState<'deploy' | 'other'>('deploy');
  const [content, setContent] = useState('');
  const [deletePrompt, setDeletePrompt] = useState<BoundPrompt | null>(null);

  const prompts = data.preset_prompts;

  async function handleCreate() {
    setBusy(true);
    setError(null);
    const payload: CreatePresetPromptInput = { type, content };
    try {
      await createPresetPrompt(appId, payload);
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
    if (!deletePrompt) return;
    setBusy(true);
    setError(null);
    try {
      await deletePresetPrompt(appId, deletePrompt.id);
      setDeletePrompt(null);
      onRefresh();
    } catch (e) {
      setError(String(e));
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
              setType('deploy');
              setContent('');
            }}
          >
            <IconPlus size={14} /> {tAdmin('addPrompt')}
          </Button>
        </div>
      )}
      {prompts.length === 0 ? (
        <p className="muted">{tc('empty')}</p>
      ) : (
        <div className="stack">
          {prompts.map((p) => (
            <div key={p.id} className="stack" style={{ gap: 4 }}>
              <div className="row-between">
                <span className="field-label">{p.type}</span>
                {isAdmin && (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setDeletePrompt(p)}
                  >
                    <IconTrash2 size={14} /> {tc('delete')}
                  </Button>
                )}
              </div>
              <p style={{ whiteSpace: 'pre-wrap' }}>{p.content}</p>
            </div>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{tAdmin('addPrompt')}</DialogTitle>
            <DialogDescription>{t('prompts')}</DialogDescription>
          </DialogHeader>
          <div className="stack">
            <Select
              value={type}
              onChange={(e) => setType(e.target.value as 'deploy' | 'other')}
              disabled={busy}
            >
              <option value="deploy">deploy</option>
              <option value="other">other</option>
            </Select>
            <Textarea
              placeholder={tAdmin('promptContent')}
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
        open={deletePrompt != null}
        onOpenChange={(o) => !o && setDeletePrompt(null)}
        title={tAdmin('deletePromptTitle')}
        description={tAdmin('deletePromptDesc')}
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [connSecretRef, setConnSecretRef] = useState('env://');
  const [allowedTablesRaw, setAllowedTablesRaw] = useState('');
  const [deleteSource, setDeleteSource] = useState<BoundDbSource | null>(null);

  const sources = data.db_sources;

  async function handleCreate() {
    setBusy(true);
    setError(null);
    const allowed_tables = allowedTablesRaw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      await createDbSource(appId, {
        name: name.trim(),
        conn_secret_ref: connSecretRef.trim(),
        allowed_tables,
      });
      setOpen(false);
      setName('');
      setConnSecretRef('env://');
      setAllowedTablesRaw('');
      onRefresh();
    } catch (e) {
      setError(String(e));
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
              setName('');
              setConnSecretRef('env://');
              setAllowedTablesRaw('');
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
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setDeleteSource(s)}
                  >
                    <IconTrash2 size={14} /> {tc('delete')}
                  </Button>
                )}
              </div>
              <p className="muted mono" style={{ fontSize: 13 }}>
                allowed_tables:{' '}
                {Array.isArray(s.allowed_tables)
                  ? (s.allowed_tables as unknown[]).join(', ')
                  : '—'}
              </p>
            </div>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{tAdmin('addDbSource')}</DialogTitle>
            <DialogDescription>{t('dbSources')}</DialogDescription>
          </DialogHeader>
          <div className="stack">
            <Input
              placeholder={tAdmin('dbSourceName')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder="env://VAR_NAME"
              value={connSecretRef}
              onChange={(e) => setConnSecretRef(e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder={tAdmin('dbAllowedTables')}
              value={allowedTablesRaw}
              onChange={(e) => setAllowedTablesRaw(e.target.value)}
              disabled={busy}
            />
          </div>
          <DialogFooter>
            <Button onClick={() => setOpen(false)} disabled={busy}>{tc('cancel')}</Button>
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={busy || !name.trim() || !connSecretRef.trim()}
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
// AI model override
// ---------------------------------------------------------------------------
//
// The application-scope AI model config lives under ``/settings/ai-models``;
// admins add/select it on `/settings` (so there's a single form code path).
// Here we only render the rows that *already* target this application and let
// admins promote one to default or remove it.

export function ModelSection({
  appId,
  onRefresh,
}: {
  appId: string;
  onRefresh: () => void;
}) {
  const t = useTranslations('application');
  const tc = useTranslations('common');
  const tAdmin = useTranslations('admin');
  const isAdmin = useUser().isAdmin;
  const [rows, setRows] = useState<Awaited<ReturnType<typeof fetchAiModelConfigs>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);

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
    const handler = () => reload();
    window.addEventListener('app-detail-refresh', handler);
    return () => window.removeEventListener('app-detail-refresh', handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scoped = useMemo(
    () => rows.filter((r) => r.scope === 'application' && r.application_id === Number(appId)),
    [rows, appId]
  );

  async function handlePromoteDefault(id: number) {
    setBusy(true);
    setError(null);
    try {
      const cur = rows.find((r) => r.id === id);
      if (!cur) return;
      await updateAiModel(id, {
        scope: 'application',
        application_id: cur.application_id,
        provider: cur.provider as 'openai' | 'anthropic',
        base_url: cur.base_url,
        api_key_ref: '',
        model: cur.model,
        is_default: true,
      });
      await reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id: number) {
    setBusy(true);
    setError(null);
    try {
      const { deleteAiModel } = await import('@/lib/api');
      await deleteAiModel(id);
      setDeleteId(null);
      await reload();
      onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <p className="page-subtitle">{t('modelOverride')}</p>
      {error && (
        <p className="muted" style={{ color: 'var(--danger)', fontSize: 13 }}>
          {error}
        </p>
      )}
      {!isAdmin ? (
        <p className="muted" style={{ fontSize: 13 }}>
          No per-application override configured — the global default is used.
        </p>
      ) : loading ? (
        <Skeleton className="h-10 w-full" />
      ) : scoped.length === 0 ? (
        <p className="muted" style={{ fontSize: 13 }}>
          {tAdmin('noAppModel')}
        </p>
      ) : (
        <div className="stack">
          {scoped.map((m) => (
            <div
              key={m.id}
              className="row-between"
              style={{
                borderTop: '1px solid var(--color-4)',
                paddingTop: 8,
              }}
            >
              <span className="mono" style={{ fontSize: 12 }}>
                #{m.id} {m.provider} · {m.model}
                {m.is_default ? ' · default' : ''}
              </span>
              <div className="row" style={{ gap: 6 }}>
                {!m.is_default && (
                  <Button size="sm" onClick={() => handlePromoteDefault(m.id)} disabled={busy}>
                    {tAdmin('setAsDefault')}
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => setDeleteId(m.id)}
                  disabled={busy}
                >
                  <IconTrash2 size={14} /> {tc('delete')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
      <ConfirmDialog
        open={deleteId != null}
        onOpenChange={(o) => !o && setDeleteId(null)}
        title={tAdmin('deleteAppModelTitle')}
        description={tAdmin('deleteAppModelDesc')}
        confirmLabel={tc('delete')}
        cancelLabel={tc('cancel')}
        destructive
        onConfirm={() => {
          if (deleteId != null) return handleDelete(deleteId);
        }}
      />
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

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchApplication(id)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [id, refreshNonce]);

  if (loading) return <ApplicationLoading />;
  if (error) return <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>;
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
