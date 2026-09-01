'use client';

import { useCallback, useEffect, useState } from 'react';
import { Plus, RefreshCw } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { TableEmptyState } from '@/components/ui/empty-state';
import { TableColumns } from '@/components/ui/table';
import { apiErrorMessage, createUser, fetchUsers, resetUserPassword, updateUser } from '@/lib/api';
import type { CurrentUser } from '@/lib/types';
import { relativeTime } from '@/lib/utils';

export default function UsersPage() {
  const t = useTranslations('users');
  const tc = useTranslations('common');
  const locale = useLocale();
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [initialPassword, setInitialPassword] = useState('');
  const [resetId, setResetId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);
  const [statusErrors, setStatusErrors] = useState<Record<number, string>>({});

  const load = useCallback(async (background = false) => {
    if (background) setRefreshing(true);
    else setLoading(true);
    try { setUsers(await fetchUsers()); setError(null); } catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally {
      if (background) setRefreshing(false);
      else setLoading(false);
    }
  }, [tc]);
  useEffect(() => { void load(); }, [load]);

  async function create() {
    setCreateError('');
    setSaving(true);
    try {
      await createUser({ username, display_name: displayName, initial_password: initialPassword });
      setUsername(''); setDisplayName(''); setInitialPassword(''); setOpen(false); await load(true);
    } catch (cause) { setCreateError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setSaving(false); }
  }
  async function reset(userId: number, password: string) {
    setBusyId(userId);
    try { await resetUserPassword(userId, password); setResetId(null); await load(true); }
    catch (cause) { throw new Error(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setBusyId(null); }
  }
  async function updateStatus(userId: number, status: string) {
    setBusyId(userId);
    setStatusErrors((current) => ({ ...current, [userId]: '' }));
    try { await updateUser(userId, { status }); await load(true); }
    catch (cause) { setStatusErrors((current) => ({ ...current, [userId]: apiErrorMessage(cause, tc('requestFailed')) })); }
    finally { setBusyId(null); }
  }

  return <main className="dashboard-page space-y-6">
    <header className="dashboard-page-header">
      <div><h1 className="page-title">{t('title')}</h1><p className="page-subtitle">{t('subtitle')}</p></div>
      <div className="flex gap-2">
        <Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load(true)}><RefreshCw size={16} /></Button>
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('newUser')}</Button>
      </div>
    </header>
    {error && users.length > 0 ? <p className="dashboard-feedback" role="alert">{error}</p> : null}
    {loading ? <ListSkeleton rows={5} columns={5} /> : error && users.length === 0 ? <TableEmptyState title={tc('requestFailed')} action={<Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw size={15} />{tc('retry')}</Button>} /> : users.length === 0 ? <TableEmptyState title={t('noUsers')} action={<Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('newUser')}</Button>} /> : <div className="operational-table"><div className="table-wrap"><table className="table"><TableColumns widths={[28, 24, 22, 26]} trailingWidth={112} /><thead><tr><th>{t('username')}</th><th>{t('name')}</th><th>{t('status')}</th><th>{t('created')}</th><th>{t('actions')}</th></tr></thead>
      <tbody>{users.map((user) => <tr key={user.id}>
        <td className="mono">{user.username}</td><td>{user.display_name || '—'}</td>
        <td>{user.is_system_admin ? t('systemAdministrator') : <><Select value={user.status} disabled={busyId === user.id} onChange={(event) => void updateStatus(user.id, event.target.value)}><option value="active">{t('statusActive')}</option><option value="disabled">{t('statusDisabled')}</option></Select>{statusErrors[user.id] ? <p className="mt-1 text-xs text-destructive" role="alert">{statusErrors[user.id]}</p> : null}</>}</td>
        <td className="table-time" title={new Date(user.created_at).toLocaleString(locale === 'zh' ? 'zh-CN' : 'en-US')}>{relativeTime(user.created_at, locale)}</td>
        <td>{!user.is_system_admin ? <Button size="sm" disabled={busyId === user.id} onClick={() => setResetId(user.id)}>{t('resetPw')}</Button> : null}</td>
      </tr>)}</tbody></table></div></div>
    }
    <Dialog open={open} onOpenChange={(value) => { if (!saving) { setOpen(value); if (!value) setCreateError(''); } }}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('newUser')}</DialogTitle></DialogHeader><div className="space-y-3">
      <label className="field"><span className="field-label">{t('username')}</span><Input value={username} onChange={(event) => setUsername(event.target.value)} /></label>
      <label className="field"><span className="field-label">{t('name')}</span><Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
      <label className="field"><span className="field-label">{t('password')}</span><Input type="password" value={initialPassword} onChange={(event) => setInitialPassword(event.target.value)} /></label>
    </div>{createError ? <p className="dashboard-feedback" role="alert">{createError}</p> : null}<DialogFooter><Button variant="outline" disabled={saving} onClick={() => { setOpen(false); setCreateError(''); }}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={username.length < 3 || initialPassword.length < 8} onClick={() => void create()}>{t('newUser')}</Button></DialogFooter></DialogContent></Dialog>
    <ResetPasswordDialog open={resetId !== null} saving={resetId !== null && busyId === resetId} onOpenChange={(value) => { if (!value) setResetId(null); }} onSubmit={(password) => reset(resetId!, password)} />
  </main>;
}

function ResetPasswordDialog({ open, saving, onOpenChange, onSubmit }: { open: boolean; saving: boolean; onOpenChange: (open: boolean) => void; onSubmit: (password: string) => Promise<void> }) {
  const t = useTranslations('users');
  const tc = useTranslations('common');
  const [password, setPassword] = useState('');
  const [submitError, setSubmitError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const busy = saving || submitting;

  useEffect(() => {
    if (open) { setPassword(''); setSubmitError(''); setSubmitting(false); }
  }, [open]);

  async function submit() {
    if (busy) return;
    setSubmitError('');
    setSubmitting(true);
    try { await onSubmit(password); }
    catch (cause) { setSubmitError(cause instanceof Error ? cause.message : tc('requestFailed')); }
    finally { setSubmitting(false); }
  }

  return <Dialog open={open} onOpenChange={(value) => { if (!busy) onOpenChange(value); }}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('resetPw')}</DialogTitle></DialogHeader><label className="field"><span className="field-label">{t('newPassword')}</span><Input type="password" value={password} disabled={busy} aria-invalid={Boolean(submitError)} aria-describedby={submitError ? 'reset-password-error' : undefined} onChange={(event) => setPassword(event.target.value)} /></label>{submitError ? <p id="reset-password-error" className="dashboard-feedback" role="alert">{submitError}</p> : null}<DialogFooter><Button variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={busy} loadingText={tc('saving')} disabled={password.length < 8} onClick={() => void submit()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>;
}
