'use client';

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, GitFork, Plus, RefreshCw, RotateCw } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { apiErrorMessage, createGitAccount, fetchGitAccounts, fetchGitAdapters, syncGitAccount } from '@/lib/api';
import type { GitAccount, GitAdapter } from '@/lib/types';

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
  }, []);
  useEffect(() => { void load(); }, [load]);
  return <main className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><p className="mb-2 text-sm text-muted-foreground">{t('eyebrow')}</p><h1 className="page-title">{t('title')}</h1><p className="page-subtitle">{t('subtitle')}</p></div><div className="flex gap-2"><Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button><Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('addAccount')}</Button></div></header>
    {error ? <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p> : null}
    {loading ? <ListSkeleton rows={5} columns={7} /> : <div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('provider')}</th><th>{t('endpoint')}</th><th>{t('account')}</th><th>{t('repositories')}</th><th>{t('state')}</th><th /></tr></thead><tbody>{accounts.map((account) => <tr key={account.id}><td className="font-medium">{account.name}</td><td>{account.adapter_id}</td><td className="mono text-xs">{account.api_url}</td><td>{account.external_account_login}</td><td>{account.repository_count}</td><td><span className="inline-flex items-center gap-1"><CheckCircle2 size={14} className={account.verification_status === 'healthy' ? 'text-success' : 'text-muted-foreground'} />{t(`verificationState.${account.verification_status}`)}</span></td><td><Button size="icon" variant="ghost" loading={syncingId === account.id} title={t('sync')} aria-label={t('sync')} disabled={account.state !== 'active'} onClick={() => { setSyncingId(account.id); void syncGitAccount(account.id).then(load).catch((cause) => toast.error(apiErrorMessage(cause, tc('requestFailed')))).finally(() => setSyncingId(null)); }}><RotateCw size={15} /></Button></td></tr>)}{!accounts.length ? <tr><td colSpan={7} className="py-8 text-center text-sm text-muted-foreground"><GitFork className="mr-2 inline" size={15} />{t('noAccounts')}</td></tr> : null}</tbody></table></div></div>}
    <AccountDialog open={open} adapters={adapters} onOpenChange={setOpen} onCreated={load} />
  </main>;
}

function AccountDialog({ open, adapters, onOpenChange, onCreated }: { open: boolean; adapters: GitAdapter[]; onOpenChange: (open: boolean) => void; onCreated: () => Promise<void> }) {
  const t = useTranslations('git'); const tc = useTranslations('common');
  const [adapterId, setAdapterId] = useState('github'); const [name, setName] = useState(''); const [apiUrl, setApiUrl] = useState(''); const [token, setToken] = useState('');
  const [saving, setSaving] = useState(false);
  const adapter = adapters.find((value) => value.id === adapterId);
  useEffect(() => { setApiUrl(''); }, [adapterId]);
  async function create() { setSaving(true); try { await createGitAccount({ adapter_id: adapterId, name, ...(apiUrl ? { api_url: apiUrl } : {}), access_token: token }); onOpenChange(false); await onCreated(); } catch (cause) { toast.error(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSaving(false); } }
  return <Dialog open={open} onOpenChange={(value) => !saving && onOpenChange(value)}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('addAccount')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('provider')}</span><Select value={adapterId} onChange={(event) => setAdapterId(event.target.value)}>{adapters.map((value) => <option key={value.id} value={value.id}>{value.display_name}</option>)}</Select></label><label className="field"><span className="field-label">{t('name')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label>{adapter?.custom_endpoint_allowed ? <label className="field"><span className="field-label">{t('apiUrl')}</span><Input placeholder={adapter.official_api_url} value={apiUrl} onChange={(event) => setApiUrl(event.target.value)} /></label> : null}<label className="field"><span className="field-label">{t('access_token')}</span><Input type="password" value={token} onChange={(event) => setToken(event.target.value)} /></label></div><DialogFooter><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={!name || !token} onClick={() => void create()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>;
}
