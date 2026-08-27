'use client';

import { useEffect, useState } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { apiErrorMessage, createUser, fetchUsers, resetUserPassword, updateUser } from '@/lib/api';
import type { CurrentUser } from '@/lib/types';

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
  const [resetPassword, setResetPassword] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function load() {
    try { setUsers(await fetchUsers()); setError(null); } catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);

  async function create() {
    setSaving(true);
    try {
      await createUser({ username, display_name: displayName, initial_password: initialPassword });
      setUsername(''); setDisplayName(''); setInitialPassword(''); setOpen(false); await load();
    } catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setSaving(false); }
  }
  async function reset(userId: number) {
    setBusyId(userId);
    try { await resetUserPassword(userId, resetPassword); setResetId(null); setResetPassword(''); await load(); }
    catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setBusyId(null); }
  }

  return <main className="dashboard-page space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><h1 className="page-title">{t('title')}</h1><p className="page-subtitle">{t('subtitle')}</p></div>
      <Button variant="primary" onClick={() => setOpen(true)}>{t('newUser')}</Button>
    </header>
    {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
    {loading ? <ListSkeleton rows={5} columns={5} /> : <div className="table-wrap"><table className="table"><thead><tr><th>{t('username')}</th><th>{t('name')}</th><th>{t('status')}</th><th>{t('created')}</th><th>{t('actions')}</th></tr></thead>
      <tbody>{users.map((user) => <tr key={user.id}>
        <td className="mono">{user.username}</td><td>{user.display_name || '-'}</td>
        <td>{user.is_system_admin ? t('systemAdministrator') : <Select value={user.status} disabled={busyId === user.id} onChange={(event) => { setBusyId(user.id); void updateUser(user.id, { status: event.target.value }).then(load).finally(() => setBusyId(null)); }}><option value="active">{t('statusActive')}</option><option value="disabled">{t('statusDisabled')}</option></Select>}</td>
        <td>{new Date(user.created_at).toLocaleString(locale === 'zh' ? 'zh-CN' : 'en-US')}</td>
        <td>{!user.is_system_admin && <Button size="sm" onClick={() => { setResetId(user.id); setResetPassword(''); }}>{t('resetPw')}</Button>}
          {resetId === user.id && <div className="mt-2 flex gap-2"><Input type="password" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} placeholder={t('newPassword')} /><Button size="sm" loading={busyId === user.id} disabled={resetPassword.length < 8} onClick={() => void reset(user.id)}>{tc('save')}</Button></div>}</td>
      </tr>)}</tbody></table></div>
    }
    <Dialog open={open} onOpenChange={(value) => !saving && setOpen(value)}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('newUser')}</DialogTitle></DialogHeader><div className="space-y-3">
      <Input placeholder={t('username')} value={username} onChange={(event) => setUsername(event.target.value)} />
      <Input placeholder={t('name')} value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
      <Input type="password" placeholder={t('password')} value={initialPassword} onChange={(event) => setInitialPassword(event.target.value)} />
    </div><DialogFooter><Button variant="outline" disabled={saving} onClick={() => setOpen(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={username.length < 3 || initialPassword.length < 8} onClick={() => void create()}>{t('newUser')}</Button></DialogFooter></DialogContent></Dialog>
  </main>;
}
