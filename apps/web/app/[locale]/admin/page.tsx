'use client';

import { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, CirclePause, CirclePlay, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { useLocale, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { apiErrorMessage, createWorkspace, fetchWorkspaces, pauseIngestion, resumeIngestion, startIngestion } from '@/lib/api';
import { Link } from '@/lib/navigation';
import type { Workspace } from '@/lib/types';

export default function WorkspacesPage() {
  const t = useTranslations('admin');
  const tc = useTranslations('common');
  const locale = useLocale();
  const [rows, setRows] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [topic, setTopic] = useState('');
  const [position, setPosition] = useState<'earliest' | 'latest'>('latest');
  const [creating, setCreating] = useState(false);
  const [transitioningId, setTransitioningId] = useState<number | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await fetchWorkspaces()); setError(''); }
    catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function create() {
    setCreating(true);
    try {
      const row = await createWorkspace({ name, ingestion_topic: topic });
      setRows((current) => [...current, row]);
      setOpen(false); setName(''); setTopic('');
      toast.success(t('workspaceCreated'));
    } catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setCreating(false); }
  }

  async function transition(row: Workspace) {
    setTransitioningId(row.id);
    try {
      const updated = row.ingestion_state === 'draft'
        ? await startIngestion(row.id, position)
        : row.ingestion_state === 'active'
          ? await pauseIngestion(row.id)
          : await resumeIngestion(row.id);
      setRows((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setTransitioningId(null); }
  }

  return <main className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="eyebrow">{t('controlPlane')}</p><h1 className="page-title">{t('workspacesTitle')}</h1><p className="page-subtitle">{t('workspacesSubtitle')}</p></div>
      <div className="flex gap-2"><Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button><Button variant="primary" onClick={() => setOpen(true)}><Plus size={16} />{t('newWorkspace')}</Button></div>
    </header>
    {error && <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</div>}
    {loading ? <ListSkeleton rows={5} columns={7} /> : <div className="operational-table">
      <div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('kafkaTopic')}</th><th>{t('policy')}</th><th>{t('investigationDepth')}</th><th>{t('ingestion')}</th><th>{t('updated')}</th><th /></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}>
          <td><Link href={`/admin/workspaces/${row.id}`} className="font-medium hover:text-link">{row.name}</Link></td>
          <td className="mono text-xs">{row.ingestion_topic}</td>
          <td>{row.model_policy_revision_id ? t('revision', { revision: row.model_policy_revision_id }) : <span className="text-warning">{t('notPublished')}</span>}</td>
          <td>{row.investigation_policy_revision_id ? t('revision', { revision: row.investigation_policy_revision_id }) : '-'}</td>
          <td><span className={`table-status table-status-${row.ingestion_state === 'active' ? 'success' : row.ingestion_state === 'paused' ? 'warning' : 'neutral'}`}><i />{t(`ingestionState.${row.ingestion_state}`)}</span></td>
          <td className="text-xs text-muted-foreground">{new Date(row.updated_at).toLocaleString(locale === 'zh' ? 'zh-CN' : 'en-US')}</td>
          <td><div className="flex justify-end gap-1"><Button size="icon" variant="ghost" loading={transitioningId === row.id} aria-label={t('ingestion')} title={t('ingestion')} onClick={() => void transition(row)}>{row.ingestion_state === 'active' ? <CirclePause size={16} /> : <CirclePlay size={16} />}</Button><Button size="icon" variant="ghost" asChild><Link href={`/admin/workspaces/${row.id}`} aria-label={tc('open')} title={tc('open')}><ArrowUpRight size={16} /></Link></Button></div></td>
        </tr>)}</tbody></table></div>
      {!loading && rows.length === 0 && <p className="p-8 text-center text-muted-foreground">{t('noWorkspaces')}</p>}
    </div>}
    <Dialog open={open} onOpenChange={(value) => !creating && setOpen(value)}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('newWorkspace')}</DialogTitle></DialogHeader><div className="space-y-4"><label className="field"><span className="field-label">{t('name')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span className="field-label">{t('kafkaTopic')}</span><Input className="mono" value={topic} onChange={(event) => setTopic(event.target.value)} /></label><label className="field"><span className="field-label">{t('initialPosition')}</span><Select value={position} onChange={(event) => setPosition(event.target.value as 'earliest' | 'latest')}><option value="latest">{t('latest')}</option><option value="earliest">{t('earliest')}</option></Select></label></div><DialogFooter><Button variant="outline" disabled={creating} onClick={() => setOpen(false)}>{tc('cancel')}</Button><Button variant="primary" loading={creating} loadingText={tc('saving')} disabled={!name.trim() || !topic.trim()} onClick={() => void create()}>{t('createWorkspace')}</Button></DialogFooter></DialogContent></Dialog>
  </main>;
}
