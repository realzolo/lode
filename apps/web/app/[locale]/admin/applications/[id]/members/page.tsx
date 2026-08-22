'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Select } from '@/components/ui/select';
import {
  Table,
  THead,
  TBody,
  Tr,
  Th,
  Td,
} from '@/components/ui/table';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  addAppMember,
  fetchAppMembers,
  fetchUsers,
  removeAppMember,
  updateAppMember,
  type AppMember,
  type AppPerm,
} from '@/lib/api';
import type { CurrentUser } from '@/lib/types';
import { ApplicationLoader } from '../sections';

const PERMS: AppPerm[] = ['read', 'analyze', 'admin'];

export default function MembersPage({ params }: { params: { id: string } }) {
  const t = useTranslations('members');
  const tc = useTranslations('common');
  const tu = useTranslations('users');

  const [members, setMembers] = useState<AppMember[]>([]);
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [removeTarget, setRemoveTarget] = useState<AppMember | null>(null);
  const [addUserId, setAddUserId] = useState<string>('');
  const [addPerm, setAddPerm] = useState<AppPerm>('read');
  const [addBusy, setAddBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [m, u] = await Promise.all([
        fetchAppMembers(params.id),
        fetchUsers(),
      ]);
      setMembers(m);
      setUsers(u);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Users not already members of this application.
  const addable = users.filter(
    (u) => !members.some((m) => m.user_id === u.id)
  );

  async function changePerm(member: AppMember, perm: AppPerm) {
    if (perm === member.perm) return;
    setBusyId(member.user_id);
    setError(null);
    try {
      await updateAppMember(params.id, member.user_id, perm);
      setMembers((prev) =>
        prev.map((m) => (m.user_id === member.user_id ? { ...m, perm } : m))
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function confirmRemove() {
    if (!removeTarget) return;
    setError(null);
    try {
      await removeAppMember(params.id, removeTarget.user_id);
      setMembers((prev) =>
        prev.filter((m) => m.user_id !== removeTarget.user_id)
      );
      setRemoveTarget(null);
    } catch (e) {
      setError(String(e));
      setRemoveTarget(null);
    }
  }

  async function handleAdd() {
    const userId = Number(addUserId);
    if (!userId) return;
    setAddBusy(true);
    setError(null);
    try {
      const created = await addAppMember(params.id, userId, addPerm);
      setMembers((prev) => [...prev, created]);
      setAddUserId('');
      setAddPerm('read');
    } catch (e) {
      setError(String(e));
    } finally {
      setAddBusy(false);
    }
  }

  return (
    <ApplicationLoader id={params.id} refreshNonce={0}>
      {() => (
        <>
          <h1 className="page-title">{t('title')}</h1>
          <p className="muted" style={{ marginTop: 4 }}>
            {t('subtitle')}
          </p>

          {error && (
            <p className="muted" style={{ color: 'var(--danger)', marginTop: 12 }}>
              {error}
            </p>
          )}

          {/* Add member */}
          <Card className="row" style={{ marginTop: 16, gap: 12, flexWrap: 'wrap' }}>
            <div className="stack" style={{ gap: 6, minWidth: 220 }}>
              <label className="field-label">{t('addMember')}</label>
              <Select
                value={addUserId}
                onChange={(e) => setAddUserId(e.target.value)}
                disabled={addBusy || addable.length === 0}
              >
                <option value="">{t('selectUser')}</option>
                {addable.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name || u.email} ({u.email})
                  </option>
                ))}
              </Select>
            </div>
            <div className="stack" style={{ gap: 6 }}>
              <label className="field-label">{t('permission')}</label>
              <Select
                value={addPerm}
                onChange={(e) => setAddPerm(e.target.value as AppPerm)}
                disabled={addBusy}
              >
                {PERMS.map((p) => (
                  <option key={p} value={p}>
                    {t(`perm${p[0].toUpperCase()}${p.slice(1)}`)}
                  </option>
                ))}
              </Select>
            </div>
            <Button
              variant="primary"
              onClick={handleAdd}
              disabled={addBusy || !addUserId}
              style={{ alignSelf: 'flex-end' }}
            >
              {t('addMember')}
            </Button>
          </Card>

          {/* Member list */}
          <Card style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}>
            {loading ? (
              <p className="muted" style={{ padding: 16 }}>
                {tc('loading')}
              </p>
            ) : members.length === 0 ? (
              <p className="muted" style={{ padding: 16 }}>
                {tc('empty')}
              </p>
            ) : (
              <Table>
                <THead>
                  <Tr>
                    <Th>{t('user')}</Th>
                    <Th>{t('globalRole')}</Th>
                    <Th>{t('permission')}</Th>
                    <Th />
                  </Tr>
                </THead>
                <TBody>
                  {members.map((m) => (
                    <Tr key={m.user_id}>
                      <Td>
                        <div className="stack" style={{ gap: 2 }}>
                          <span>{m.name || m.email}</span>
                          <span className="mono muted" style={{ fontSize: 12 }}>
                            {m.email}
                          </span>
                        </div>
                      </Td>
                      <Td>
                        <Badge variant={m.role === 'admin' ? 'accent' : 'default'}>
                          {m.role === 'admin' ? tu('roleAdmin') : tu('roleUser')}
                        </Badge>
                      </Td>
                      <Td>
                        <Select
                          value={m.perm}
                          disabled={busyId === m.user_id}
                          onChange={(e) => changePerm(m, e.target.value as AppPerm)}
                        >
                          {PERMS.map((p) => (
                            <option key={p} value={p}>
                              {t(`perm${p[0].toUpperCase()}${p.slice(1)}`)}
                            </option>
                          ))}
                        </Select>
                      </Td>
                      <Td className="row" style={{ justifyContent: 'flex-end' }}>
                        <Button
                          variant="default"
                          onClick={() => setRemoveTarget(m)}
                          disabled={busyId === m.user_id}
                        >
                          {t('remove')}
                        </Button>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            )}
          </Card>

          <ConfirmDialog
            open={removeTarget !== null}
            onOpenChange={(open) => !open && setRemoveTarget(null)}
            title={t('removeTitle')}
            description={t('removeDesc')}
            confirmLabel={t('remove')}
            destructive
            onConfirm={confirmRemove}
          />
        </>
      )}
    </ApplicationLoader>
  );
}
