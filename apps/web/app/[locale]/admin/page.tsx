'use client';

import { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { useLocale, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { TableEmptyState } from '@/components/ui/empty-state';
import { TableColumns } from '@/components/ui/table';
import { apiErrorMessage, createWorkspace, fetchWorkspaces } from '@/lib/api';
import { Link } from '@/lib/navigation';
import { relativeTime } from '@/lib/utils';
import type { Workspace } from '@/lib/types';

export default function WorkspacesPage() {
  const t = useTranslations('admin');
  const tc = useTranslations('common');
  const locale = useLocale();
  const [rows, setRows] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [topic, setTopic] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const load = useCallback(async (background = false) => {
    background ? setRefreshing(true) : setLoading(true);
    try { setRows(await fetchWorkspaces()); setError(''); }
    catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setLoading(false); setRefreshing(false); }
  }, [tc]);
  useEffect(() => { void load(); }, [load]);

  async function create() {
    setCreateError('');
    setCreating(true);
    try {
      const row = await createWorkspace({ name, description, ingestion_topic: topic });
      setRows((current) => [...current, row]);
      setOpen(false); setName(''); setDescription(''); setTopic('');
      toast.success(t('workspaceCreated'));
    } catch (cause) { setCreateError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setCreating(false); }
  }

  return <main className="dashboard-page space-y-6">
    <header className="dashboard-page-header">
      <div><h1 className="page-title">{t('workspacesTitle')}</h1><p className="page-subtitle">{t('workspacesSubtitle')}</p></div>
      <div className="flex gap-2"><Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load(true)}><RefreshCw size={16} /></Button><Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('newWorkspace')}</Button></div>
    </header>
    {error && rows.length > 0 ? <div className="dashboard-feedback" role="alert">{error}</div> : null}
    {loading ? <ListSkeleton rows={5} columns={6} /> : error && rows.length === 0 ? <TableEmptyState title={tc('requestFailed')} action={<Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw size={15} />{tc('retry')}</Button>} /> : rows.length === 0 ? <TableEmptyState title={t('noWorkspaces')} action={<Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('newWorkspace')}</Button>} /> : <div className="operational-table">
      <div className="table-wrap"><table className="table"><TableColumns widths={[28, 24, 14, 14, 20]} trailingWidth={64} /><thead><tr><th>{t('name')}</th><th>{t('kafkaTopic')}</th><th>{t('policy')}</th><th>{t('ingestion')}</th><th>{t('updated')}</th><th><span className="sr-only">{tc('actions')}</span></th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}>
          <td><Link href={`/admin/workspaces/${row.id}`} className="table-record-link">{row.name}</Link></td>
          <td className="mono text-xs">{row.ingestion_topic}</td>
          <td><span className={`table-status table-status-${row.model_policy_revision_id ? 'success' : 'warning'}`}><i aria-hidden="true" />{row.model_policy_revision_id ? t('published') : t('notPublished')}</span></td>
          <td><span className={`table-status table-status-${row.ingestion_state === 'active' ? 'success' : row.ingestion_state === 'paused' ? 'warning' : 'neutral'}`}><i aria-hidden="true" />{t(`ingestionState.${row.ingestion_state}`)}</span></td>
          <td className="table-time text-xs text-muted-foreground" title={new Date(row.updated_at).toLocaleString(locale === 'zh' ? 'zh-CN' : 'en-US')}>{relativeTime(row.updated_at, locale)}</td>
          <td><div className="flex justify-end"><Button size="icon" variant="ghost" asChild aria-label={tc('open')} title={tc('open')}><Link href={`/admin/workspaces/${row.id}`}><ArrowUpRight size={16} /></Link></Button></div></td>
        </tr>)}</tbody></table></div>
    </div>}
    <Dialog open={open} onOpenChange={(value) => { if (!creating) { setOpen(value); if (!value) setCreateError(''); } }}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('newWorkspace')}</DialogTitle></DialogHeader><div className="space-y-4"><label className="field"><span className="field-label">{t('name')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span className="field-label">{t('workspaceDescription')}</span><Textarea value={description} maxLength={1000} onChange={(event) => setDescription(event.target.value)} /></label><label className="field"><span className="field-label">{t('kafkaTopic')}</span><Input className="mono" value={topic} onChange={(event) => setTopic(event.target.value)} /></label></div>{createError ? <p className="dashboard-feedback" role="alert">{createError}</p> : null}<DialogFooter><Button variant="outline" disabled={creating} onClick={() => { setOpen(false); setCreateError(''); }}>{tc('cancel')}</Button><Button variant="primary" loading={creating} loadingText={tc('saving')} disabled={!name.trim() || !topic.trim()} onClick={() => void create()}>{t('createWorkspace')}</Button></DialogFooter></DialogContent></Dialog>
  </main>;
}
