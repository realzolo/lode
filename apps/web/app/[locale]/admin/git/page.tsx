'use client';

import { useCallback, useEffect, useState } from 'react';
import { Plus, RefreshCw, RotateCw } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { TableEmptyState } from '@/components/ui/empty-state';
import { TableColumns } from '@/components/ui/table';
import { apiErrorMessage, createGitAccount, fetchGitAccounts, fetchGitAdapters, syncGitAccount } from '@/lib/api';
import type { GitAccount, GitAdapter } from '@/lib/types';

function verificationTone(status: GitAccount['verification_status']) {
  return status === 'healthy' ? 'success' : status === 'unavailable' ? 'danger' : 'neutral';
}

export default function GitAdministrationPage() {
  const t = useTranslations('git');
  const tc = useTranslations('common');
  const [adapters, setAdapters] = useState<GitAdapter[]>([]);
  const [accounts, setAccounts] = useState<GitAccount[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncingId, setSyncingId] = useState<number | null>(null);
  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [adapterRows, accountRows] = await Promise.all([fetchGitAdapters(), fetchGitAccounts()]);
      setAdapters(adapterRows); setAccounts(accountRows); setError('');
    } catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setLoading(false); setRefreshing(false); }
  }, [tc]);
  useEffect(() => { void load(); }, [load]);
  return <main className="dashboard-page space-y-6">
    <header className="dashboard-page-header"><div><h1 className="page-title">{t('title')}</h1><p className="page-subtitle">{t('subtitle')}</p></div><div className="flex gap-2"><Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button><Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('addAccount')}</Button></div></header>
    {error && accounts.length > 0 ? <p className="dashboard-feedback" role="alert">{error}</p> : null}
    {loading ? <ListSkeleton rows={5} columns={7} /> : error && accounts.length === 0 ? <TableEmptyState title={tc('requestFailed')} action={<Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw size={15} />{tc('retry')}</Button>} /> : accounts.length === 0 ? <TableEmptyState title={t('noAccounts')} action={<Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('addAccount')}</Button>} /> : <div className="operational-table"><div className="table-wrap"><table className="table"><TableColumns widths={[18, 12, 26, 18, 12, 14]} trailingWidth={64} /><thead><tr><th>{t('name')}</th><th>{t('provider')}</th><th>{t('endpoint')}</th><th>{t('account')}</th><th>{t('repositories')}</th><th>{t('state')}</th><th><span className="sr-only">{tc('actions')}</span></th></tr></thead><tbody>{accounts.map((account) => <tr key={account.id}><td className="font-medium">{account.name}</td><td>{adapters.find((adapter) => adapter.id === account.adapter_id)?.display_name ?? t('unknownProvider')}</td><td className="mono text-xs" title={account.api_url}>{account.api_url}</td><td>{account.external_account_login}</td><td className="table-number">{account.repository_count}</td><td><span className={`table-status table-status-${verificationTone(account.verification_status)}`}><i aria-hidden="true" />{t(`verificationState.${account.verification_status}`)}</span></td><td><Button size="icon" variant="ghost" loading={syncingId === account.id} title={t('sync')} aria-label={t('sync')} disabled={account.state !== 'active'} onClick={() => { setSyncingId(account.id); void syncGitAccount(account.id).then(load).catch((cause) => toast.error(apiErrorMessage(cause, tc('requestFailed')))).finally(() => setSyncingId(null)); }}><RotateCw size={15} /></Button></td></tr>)}</tbody></table></div></div>}
    <AccountDialog open={open} adapters={adapters} onOpenChange={setOpen} onCreated={load} />
  </main>;
}

function AccountDialog({ open, adapters, onOpenChange, onCreated }: { open: boolean; adapters: GitAdapter[]; onOpenChange: (open: boolean) => void; onCreated: () => Promise<void> }) {
  const t = useTranslations('git'); const tc = useTranslations('common');
  const [adapterId, setAdapterId] = useState<GitAdapter['id']>('github'); const [name, setName] = useState(''); const [apiUrl, setApiUrl] = useState(''); const [token, setToken] = useState('');
  const [saving, setSaving] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const adapter = adapters.find((value) => value.id === adapterId);
  useEffect(() => { if (open && adapter?.official_api_url) setApiUrl(adapter.official_api_url); }, [adapter?.official_api_url, open]);
  useEffect(() => { if (!open) setSubmitError(''); }, [open]);
  async function create() { setSubmitError(''); setSaving(true); try { const trimmedApiUrl = apiUrl.trim(); await createGitAccount({ adapter_id: adapterId, name: name.trim(), ...(adapter?.custom_endpoint_allowed && trimmedApiUrl ? { api_url: trimmedApiUrl } : {}), access_token: token }); onOpenChange(false); await onCreated(); } catch (cause) { setSubmitError(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSaving(false); } }
  return <Dialog open={open} onOpenChange={(value) => !saving && onOpenChange(value)}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('addAccount')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('provider')}</span><Select value={adapterId} onChange={(event) => setAdapterId(event.target.value as GitAdapter['id'])}>{adapters.map((value) => <option key={value.id} value={value.id}>{value.display_name}</option>)}</Select></label><label className="field"><span className="field-label">{t('name')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label>{adapter?.custom_endpoint_allowed ? <label className="field"><span className="field-label">{t('apiUrl')}</span><Input className="mono" value={apiUrl} onChange={(event) => setApiUrl(event.target.value)} /></label> : null}<label className="field"><span className="field-label">{t('access_token')}</span><Input type="password" value={token} onChange={(event) => setToken(event.target.value)} /></label></div>{submitError ? <p className="dashboard-feedback" role="alert">{submitError}</p> : null}<DialogFooter><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={!name.trim() || !token} onClick={() => void create()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>;
}
