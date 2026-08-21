'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import {
  createInvite,
  createUser,
  deleteUser,
  fetchUsers,
  resetUserPassword,
  updateUser,
} from '@/lib/api';
import { useUser } from '@/lib/user-context';
import type { CurrentUser } from '@/lib/types';

export default function UsersPage() {
  const t = useTranslations('users');
  const tc = useTranslations('common');
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // create-user form
  const [showCreate, setShowCreate] = useState(false);
  const [createEmail, setCreateEmail] = useState('');
  const [createName, setCreateName] = useState('');
  const [createRole, setCreateRole] = useState('user');
  const [createPassword, setCreatePassword] = useState('');
  const [busyCreate, setBusyCreate] = useState(false);

  // invite form
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [busyInvite, setBusyInvite] = useState(false);

  // per-row reset-password
  const [resetId, setResetId] = useState<number | null>(null);
  const [resetPassword, setResetPassword] = useState('');

  const isAdmin = useUser().isAdmin;

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setUsers(await fetchUsers());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (isAdmin) load();
    else setLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!isAdmin) {
    return (
      <>
        <h1 className="page-title">{t('title')}</h1>
        <p className="muted">{tc('empty')}</p>
      </>
    );
  }

  async function handleCreate() {
    setBusyCreate(true);
    setError(null);
    try {
      await createUser({
        email: createEmail,
        name: createName,
        role: createRole,
        password: createPassword,
      });
      setCreateEmail('');
      setCreateName('');
      setCreateRole('user');
      setCreatePassword('');
      setShowCreate(false);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyCreate(false);
    }
  }

  async function handleInvite() {
    setBusyInvite(true);
    setError(null);
    setInviteLink(null);
    try {
      const inv = await createInvite(inviteEmail);
      const link = `${window.location.origin}/accept-invite?token=${encodeURIComponent(inv.token)}`;
      setInviteLink(link);
      setInviteEmail('');
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyInvite(false);
    }
  }

  async function handleReset(id: number) {
    setError(null);
    try {
      await resetUserPassword(id, resetPassword);
      setResetId(null);
      setResetPassword('');
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm(t('delete') + '?')) return;
    setError(null);
    try {
      await deleteUser(id);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleRoleChange(id: number, role: string) {
    setError(null);
    try {
      await updateUser(id, { role });
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleStatusChange(id: number, status: string) {
    setError(null);
    try {
      await updateUser(id, { status });
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <p className="page-subtitle">{t('subtitle')}</p>
      {loading && <p className="muted">{tc('loading')}</p>}
      {error && (
        <p className="muted" style={{ color: 'var(--danger, #f87171)' }}>
          {error}
        </p>
      )}

      <div className="row" style={{ gap: 8, marginTop: 16 }}>
        <Button variant="primary" onClick={() => setShowCreate((v) => !v)}>
          {t('newUser')}
        </Button>
        <Button onClick={() => { setShowInvite((v) => !v); setInviteLink(null); }}>
          {t('invite')}
        </Button>
      </div>

      {showCreate && (
        <Card style={{ marginTop: 16 }}>
          <div className="stack">
            <Input
              type="email"
              placeholder={t('email')}
              value={createEmail}
              onChange={(e) => setCreateEmail(e.target.value)}
            />
            <Input
              placeholder={t('name')}
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
            />
            <Select value={createRole} onChange={(e) => setCreateRole(e.target.value)}>
              <option value="user">{t('roleUser')}</option>
              <option value="admin">{t('roleAdmin')}</option>
            </Select>
            <Input
              type="password"
              placeholder={t('password')}
              value={createPassword}
              onChange={(e) => setCreatePassword(e.target.value)}
            />
            <Button variant="primary" onClick={handleCreate} disabled={busyCreate || !createEmail || createPassword.length < 8}>
              {t('newUser')}
            </Button>
          </div>
        </Card>
      )}

      {showInvite && (
        <Card style={{ marginTop: 16 }}>
          <div className="stack">
            <Input
              type="email"
              placeholder={t('email')}
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
            />
            <Button variant="primary" onClick={handleInvite} disabled={busyInvite || !inviteEmail}>
              {t('invite')}
            </Button>
            {inviteLink && (
              <div className="stack" style={{ marginTop: 8 }}>
                <p className="muted">{t('inviteSent')}</p>
                <code className="mono" style={{ fontSize: 12, wordBreak: 'break-all' }}>
                  {inviteLink}
                </code>
                <Button
                  size="sm"
                  onClick={() => {
                    navigator.clipboard?.writeText(inviteLink);
                  }}
                >
                  {t('copyLink')}
                </Button>
              </div>
            )}
          </div>
        </Card>
      )}

      <Card style={{ marginTop: 16 }}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('email')}</th>
                <th>{t('name')}</th>
                <th>{t('role')}</th>
                <th>{t('status')}</th>
                <th>{t('created')}</th>
                <th>{t('actions')}</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="mono">{u.email}</td>
                  <td>{u.name || '—'}</td>
                  <td>
                    <Select
                      value={u.role}
                      onChange={(e) => handleRoleChange(u.id, e.target.value)}
                    >
                      <option value="user">{t('roleUser')}</option>
                      <option value="admin">{t('roleAdmin')}</option>
                    </Select>
                  </td>
                  <td>
                    <Select
                      value={u.status}
                      onChange={(e) => handleStatusChange(u.id, e.target.value)}
                    >
                      <option value="active">{t('statusActive')}</option>
                      <option value="disabled">{t('statusDisabled')}</option>
                      <option value="pending">{t('statusPending')}</option>
                    </Select>
                  </td>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {new Date(u.created_at).toLocaleString()}
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <Button size="sm" onClick={() => { setResetId(u.id); setResetPassword(''); }}>
                        {t('resetPw')}
                      </Button>
                      <Button size="sm" variant="primary" onClick={() => handleDelete(u.id)}>
                        {t('delete')}
                      </Button>
                    </div>
                    {resetId === u.id && (
                      <div className="row" style={{ gap: 6, marginTop: 6 }}>
                        <Input
                          type="password"
                          placeholder={t('newPassword')}
                          value={resetPassword}
                          onChange={(e) => setResetPassword(e.target.value)}
                        />
                        <Button size="sm" variant="primary" onClick={() => handleReset(u.id)} disabled={resetPassword.length < 8}>
                          {tc('save')}
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
