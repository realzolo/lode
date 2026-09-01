'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { MoreHorizontal, Plus, RefreshCw, Search, Trash2, UserRound, X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuCheck,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { TableEmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { SearchableSelect } from '@/components/ui/searchable-select';
import { Select } from '@/components/ui/select';
import { TableColumns } from '@/components/ui/table';
import {
  apiErrorMessage,
  fetchUsers,
  fetchWorkspaceMembers,
  putWorkspaceMember,
  removeWorkspaceMember,
} from '@/lib/api';
import type { CurrentUser, WorkspaceMember } from '@/lib/types';

type Permission = 'viewer' | 'operator';
type StatusFilter = 'all' | 'active' | 'disabled';
type PermissionFilter = 'all' | Permission;

export function WorkspaceMembersTab({ workspaceId }: { workspaceId: string }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [search, setSearch] = useState('');
  const [permissionFilter, setPermissionFilter] = useState<PermissionFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [rowBusy, setRowBusy] = useState<Record<number, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<WorkspaceMember | null>(null);
  const [userId, setUserId] = useState('');
  const [permission, setPermission] = useState<Permission>('viewer');
  const [adding, setAdding] = useState(false);

  const load = useCallback(async (background = false) => {
    background ? setRefreshing(true) : setLoading(true);
    try {
      const [nextMembers, nextUsers] = await Promise.all([
        fetchWorkspaceMembers(workspaceId),
        fetchUsers(),
      ]);
      setMembers(nextMembers);
      setUsers(nextUsers.filter((user) => !user.is_system_admin));
      setLoadError('');
    } catch (cause) {
      setLoadError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [tc, workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return members.filter((member) => {
      if (permissionFilter !== 'all' && member.permission !== permissionFilter) return false;
      if (statusFilter !== 'all' && member.status !== statusFilter) return false;
      return !query || `${member.display_name} ${member.username}`.toLowerCase().includes(query);
    });
  }, [members, permissionFilter, search, statusFilter]);

  const availableUsers = useMemo(() => {
    const memberIds = new Set(members.map((member) => member.user_id));
    return users.filter((user) => user.status === 'active' && !memberIds.has(user.id));
  }, [members, users]);

  const filtersActive = Boolean(search.trim()) || permissionFilter !== 'all' || statusFilter !== 'all';

  function clearFilters() {
    setSearch('');
    setPermissionFilter('all');
    setStatusFilter('all');
  }

  async function updatePermission(member: WorkspaceMember, next: Permission) {
    if (member.permission === next || rowBusy[member.user_id]) return;
    setRowBusy((current) => ({ ...current, [member.user_id]: true }));
    setRowErrors((current) => ({ ...current, [member.user_id]: '' }));
    try {
      const updated = await putWorkspaceMember(workspaceId, member.user_id, next);
      setMembers((current) => current.map((value) => value.user_id === member.user_id ? updated : value));
    } catch (cause) {
      setRowErrors((current) => ({
        ...current,
        [member.user_id]: apiErrorMessage(cause, tc('requestFailed')),
      }));
    } finally {
      setRowBusy((current) => ({ ...current, [member.user_id]: false }));
    }
  }

  async function addMember() {
    if (!userId) return;
    setAdding(true);
    try {
      await putWorkspaceMember(workspaceId, Number(userId), permission);
      await load(true);
      setAddOpen(false);
      setUserId('');
      setPermission('viewer');
      toast.success(t('memberAdded'));
    } catch (cause) {
      toast.error(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setAdding(false);
    }
  }

  async function removeMember(member: WorkspaceMember) {
    setRowBusy((current) => ({ ...current, [member.user_id]: true }));
    setRowErrors((current) => ({ ...current, [member.user_id]: '' }));
    try {
      await removeWorkspaceMember(workspaceId, member.user_id);
      setMembers((current) => current.filter((value) => value.user_id !== member.user_id));
    } catch (cause) {
      const message = apiErrorMessage(cause, tc('requestFailed'));
      setRowErrors((current) => ({ ...current, [member.user_id]: message }));
      throw new Error(message);
    } finally {
      setRowBusy((current) => ({ ...current, [member.user_id]: false }));
    }
  }

  return (
    <section className="space-y-4" aria-labelledby="members-title">
      <h2 id="members-title" className="sr-only">{t('members')}</h2>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">{t('membersDescription')}</p>
        <div className="flex items-center gap-2">
          <span className="rounded-sm border bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            {members.length}
          </span>
          <Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load(true)}>
            <RefreshCw size={15} />
          </Button>
          <Button size="sm" variant="primary" onClick={() => setAddOpen(true)}>
            <Plus size={15} />
            {t('addMember')}
          </Button>
        </div>
      </div>

      <div className="dashboard-filterbar">
        <label className="dashboard-search">
          <span className="sr-only">{t('searchMembers')}</span>
          <Search size={15} />
          <Input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('searchMembersPlaceholder')}
          />
        </label>
        <Select
          aria-label={t('permissionFilter')}
          value={permissionFilter}
          onChange={(event) => setPermissionFilter(event.target.value as PermissionFilter)}
          className="dashboard-filter-select"
        >
          <option value="all">{t('allPermissions')}</option>
          <option value="viewer">{t('viewer')}</option>
          <option value="operator">{t('operator')}</option>
        </Select>
        <Select
          aria-label={t('statusFilter')}
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
          className="dashboard-filter-select"
        >
          <option value="all">{t('allStatuses')}</option>
          <option value="active">{t('memberStatus.active')}</option>
          <option value="disabled">{t('memberStatus.disabled')}</option>
        </Select>
        {filtersActive ? (
          <Button
            size="icon"
            variant="ghost"
            aria-label={tc('clearFilters')}
            title={tc('clearFilters')}
            onClick={clearFilters}
          >
            <X size={16} />
          </Button>
        ) : null}
      </div>

      {loading ? (
        <ListSkeleton rows={4} columns={5} />
      ) : loadError ? (
        <TableEmptyState
          title={t('membersLoadFailed')}
          description={loadError}
          action={<Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw />{tc('retry')}</Button>}
        />
      ) : filtered.length === 0 ? (
        <TableEmptyState
          icon={filtersActive ? <Search size={20} /> : <UserRound size={20} />}
          title={filtersActive ? t('noFilteredMembers') : t('noMembers')}
          description={filtersActive ? t('noFilteredMembersDescription') : t('noMembersDescription')}
          action={filtersActive ? <Button size="sm" variant="outline" onClick={clearFilters}>{tc('clearFilters')}</Button> : undefined}
        />
      ) : (
        <div className="operational-table">
          <div className="table-wrap">
            <table className="table">
              <TableColumns widths={[34, 26, 20, 20]} trailingWidth={64} />
              <thead>
                <tr>
                  <th>{t('member')}</th>
                  <th>{t('username')}</th>
                  <th>{t('permissions')}</th>
                  <th>{t('accountStatus')}</th>
                  <th><span className="sr-only">{tc('actions')}</span></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((member) => (
                  <tr key={member.user_id}>
                    <td className="font-medium">
                      {member.display_name}
                      {rowErrors[member.user_id] ? (
                        <p className="mt-1 text-xs font-normal text-destructive" role="alert">{rowErrors[member.user_id]}</p>
                      ) : null}
                    </td>
                    <td className="mono text-xs">@{member.username}</td>
                    <td>{t(member.permission)}</td>
                    <td>
                      <span className={`table-status${member.status === 'active' ? ' table-status-success' : ''}`}>
                        <i aria-hidden="true" />
                        {t(`memberStatus.${member.status}`)}
                      </span>
                    </td>
                    <td>
                      <div className="flex justify-end">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              size="icon"
                              variant="ghost"
                              loading={rowBusy[member.user_id]}
                              aria-label={t('memberActions', { name: member.display_name })}
                              title={tc('actions')}
                            >
                              <MoreHorizontal size={15} />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onSelect={() => void updatePermission(member, 'viewer')}>
                              {t('setViewer')}
                              <DropdownMenuCheck visible={member.permission === 'viewer'} />
                            </DropdownMenuItem>
                            <DropdownMenuItem onSelect={() => void updatePermission(member, 'operator')}>
                              {t('setOperator')}
                              <DropdownMenuCheck visible={member.permission === 'operator'} />
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem destructive onSelect={() => setRemoveTarget(member)}>
                              <Trash2 />
                              {t('removeMember')}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Dialog open={addOpen} onOpenChange={(open) => !adding && setAddOpen(open)}>
        <DialogContent variant="drawer">
          <DialogHeader>
            <DialogTitle>{t('addMember')}</DialogTitle>
            <DialogDescription>{t('addMemberDescription')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <label className="field">
              <span className="field-label">{t('member')}</span>
              <SearchableSelect
                value={userId}
                onValueChange={setUserId}
                options={availableUsers.map((user) => ({
                  value: String(user.id),
                  label: user.display_name,
                  description: `@${user.username}`,
                  keywords: user.username,
                }))}
                placeholder={t('selectUser')}
                searchPlaceholder={t('searchUsersPlaceholder')}
                emptyMessage={t('noAvailableUsers')}
                disabled={adding}
                ariaLabel={t('selectUser')}
              />
            </label>
            <label className="field">
              <span className="field-label">{t('permissions')}</span>
              <Select
                value={permission}
                disabled={adding}
                onChange={(event) => setPermission(event.target.value as Permission)}
              >
                <option value="viewer">{t('viewer')}</option>
                <option value="operator">{t('operator')}</option>
              </Select>
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={adding} onClick={() => setAddOpen(false)}>{tc('cancel')}</Button>
            <Button variant="primary" loading={adding} loadingText={tc('saving')} disabled={!userId} onClick={() => void addMember()}>
              {t('addMember')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(open) => !open && setRemoveTarget(null)}
        title={t('removeMemberTitle')}
        description={removeTarget ? t('removeMemberDescription', { name: removeTarget.display_name }) : undefined}
        confirmLabel={t('removeMember')}
        cancelLabel={tc('cancel')}
        destructive
        successMessage={t('memberRemoved')}
        errorMessage={tc('requestFailed')}
        onConfirm={async () => {
          if (removeTarget) await removeMember(removeTarget);
        }}
      />
    </section>
  );
}
