'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
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
  createInvite,
  createUser,
  deleteUser,
  fetchInvites,
  fetchUsers,
  resetUserPassword,
  updateUser,
} from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { IconPlus, IconMail, IconTrash2 } from '@/components/icons';
import type { CurrentUser, Invite } from '@/lib/types';

export default function UsersPage() {
  const t = useTranslations('users');
  const tc = useTranslations('common');
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
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
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const isAdmin = useUser().isAdmin;

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [nextUsers, nextInvites] = await Promise.all([fetchUsers(), fetchInvites()]);
      setUsers(nextUsers);
      setInvites([...nextInvites].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)));
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
      const link = inviteLinkFor(inv);
      setInviteLink(link);
      setInvites((previous) => [inv, ...previous]);
      setInviteEmail('');
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyInvite(false);
    }
  }

  function inviteLinkFor(invite: Invite) {
    return `${window.location.origin}/accept-invite?token=${encodeURIComponent(invite.token)}`;
  }

  async function copyInviteLink(link = inviteLink) {
    if (!link) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(link);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = link;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('copy command was rejected');
      }
      toast.success(tc('copied'));
    } catch {
      toast.error(tc('copyFailed'));
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
    setError(null);
    try {
      await deleteUser(id);
      setUsers((previous) => previous.filter((user) => user.id !== id));
    } catch (e) {
      setError(String(e));
      throw e;
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
      {loading && users.length === 0 && (
        <Card style={{ marginTop: 16 }} aria-busy="true">
          <div className="stack" style={{ gap: 12 }}>
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton className="h-3.5 w-44" />
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-8 w-20" />
                <Skeleton className="h-8 w-20" />
                <Skeleton className="h-3.5 w-24" />
                <div className="ml-auto flex gap-2">
                  <Skeleton className="h-7 w-14" />
                  <Skeleton className="h-7 w-14" />
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
      {error && (
        <div className="dashboard-error" role="alert">
          <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>
          <Button variant="outline" size="sm" onClick={() => void load()}>{tc('retry')}</Button>
        </div>
      )}

      <div className="row" style={{ gap: 8, marginTop: 16 }}>
        <Button variant="primary" onClick={() => setShowCreate(true)}>
          <IconPlus size={16} /> {t('newUser')}
        </Button>
        <Button onClick={() => { setShowInvite(true); setInviteLink(null); }}>
          <IconMail size={16} /> {t('invite')}
        </Button>
      </div>

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('newUser')}</DialogTitle>
          </DialogHeader>
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
          </div>
          <DialogFooter>
            <Button variant="default" onClick={() => setShowCreate(false)}>{tc('cancel')}</Button>
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={busyCreate || !createEmail || createPassword.length < 8}
            >
              {t('newUser')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={showInvite} onOpenChange={setShowInvite}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('invite')}</DialogTitle>
            <DialogDescription>{t('subtitle')}</DialogDescription>
          </DialogHeader>
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
                  onClick={() => void copyInviteLink()}
                >
                  {t('copyLink')}
                </Button>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="default" onClick={() => setShowInvite(false)}>{tc('cancel')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {!loading && !error && users.length === 0 ? (
        <div className="experience-state"><p className="muted">{tc('empty')}</p></div>
      ) : users.length > 0 ? (
        <div className="operational-table">
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
                      <Button size="sm" variant="destructive" onClick={() => setDeleteId(u.id)}>
                        <IconTrash2 size={14} /> {t('delete')}
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
        </div>
      ) : null}

      <section className="invite-list" aria-label={t('invites')}>
        <div className="invite-list-heading">
          <h2>{t('invites')}</h2>
          <span className="muted">{invites.length}</span>
        </div>
        {invites.length === 0 ? (
          <p className="muted">{t('invitesEmpty')}</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('email')}</th>
                  <th>{t('status')}</th>
                  <th>{t('created')}</th>
                  <th>{t('actions')}</th>
                </tr>
              </thead>
              <tbody>
                {invites.map((invite) => (
                  <tr key={invite.id}>
                    <td className="mono">{invite.email}</td>
                    <td>
                      <Badge variant={invite.status === 'accepted' ? 'success' : 'warning'}>
                        {invite.status === 'accepted' ? t('inviteAccepted') : t('invitePending')}
                      </Badge>
                    </td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {new Date(invite.created_at).toLocaleString()}
                    </td>
                    <td>
                      {invite.status === 'pending' && (
                        <Button size="sm" onClick={() => void copyInviteLink(inviteLinkFor(invite))}>
                          {t('copyLink')}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={deleteId != null}
        onOpenChange={(open) => !open && setDeleteId(null)}
        title={t('deleteUserTitle')}
        description={t('deleteUserDesc')}
        confirmLabel={tc('delete')}
        cancelLabel={tc('cancel')}
        destructive
        onConfirm={() => {
          if (deleteId != null) return handleDelete(deleteId);
        }}
      />
    </>
  );
}
