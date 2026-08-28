'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { MoreHorizontal, Plus, RefreshCw, Search, Trash2, UserRound } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
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
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { SearchableSelect } from '@/components/ui/searchable-select';
import { Select } from '@/components/ui/select';
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

function initials(member: Pick<WorkspaceMember, 'display_name' | 'username'>): string {
  const parts = member.display_name.trim().split(/\s+/).filter(Boolean);
  const value = parts.length > 1 ? `${parts[0][0]}${parts.at(-1)?.[0] ?? ''}` : parts[0]?.slice(0, 2);
  return (value || member.username.slice(0, 2)).toUpperCase();
}

export function WorkspaceMembersTab({ workspaceId }: { workspaceId: string }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [loading, setLoading] = useState(true);
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

  const load = useCallback(async () => {
    setLoading(true);
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
      await load();
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
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 id="members-title" className="text-base font-semibold">{t('members')}</h2>
            <span className="rounded-sm border bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {members.length}
            </span>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t('membersDescription')}</p>
        </div>
        <Button size="sm" variant="primary" onClick={() => setAddOpen(true)}>
          <Plus size={15} />
          {t('addMember')}
        </Button>
      </div>

      <div className="flex flex-col gap-2 border-y py-3 sm:flex-row">
        <label className="relative min-w-0 flex-1">
          <span className="sr-only">{t('searchMembers')}</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('searchMembersPlaceholder')}
            className="pl-9"
          />
        </label>
        <Select
          aria-label={t('permissionFilter')}
          value={permissionFilter}
          onChange={(event) => setPermissionFilter(event.target.value as PermissionFilter)}
          className="w-full sm:w-44"
        >
          <option value="all">{t('allPermissions')}</option>
          <option value="viewer">{t('viewer')}</option>
          <option value="operator">{t('operator')}</option>
        </Select>
        <Select
          aria-label={t('statusFilter')}
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
          className="w-full sm:w-40"
        >
          <option value="all">{t('allStatuses')}</option>
          <option value="active">{t('memberStatus.active')}</option>
          <option value="disabled">{t('memberStatus.disabled')}</option>
        </Select>
      </div>

      <div className="overflow-hidden rounded-md border">
        {loading ? (
          <div className="space-y-px bg-border" aria-busy="true">
            {[0, 1, 2].map((row) => <div key={row} className="h-[68px] animate-pulse bg-card" />)}
          </div>
        ) : loadError ? (
          <EmptyState
            title={t('membersLoadFailed')}
            description={loadError}
            action={<Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw />{tc('retry')}</Button>}
          />
        ) : filtered.length ? (
          <ul className="divide-y" aria-label={t('members')}>
            {filtered.map((member) => (
              <li key={member.user_id} className="flex min-h-[68px] items-center gap-3 px-3 py-2 sm:px-4">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-full border bg-muted text-xs font-semibold">
                  {initials(member)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-center">
                    <span className="min-w-0 truncate text-sm font-medium" title={member.display_name}>
                      {member.display_name}
                    </span>
                  </div>
                  <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                    <span className="min-w-0 truncate" title={`@${member.username}`}>@{member.username}</span>
                    <span className="shrink-0" aria-hidden="true">·</span>
                    <Badge className="shrink-0" variant={member.status === 'active' ? 'success' : 'default'}>
                      {t(`memberStatus.${member.status}`)}
                    </Badge>
                    <span className="shrink-0" aria-hidden="true">·</span>
                    <span className="shrink-0">{t(member.permission)}</span>
                  </div>
                  {rowErrors[member.user_id] ? (
                    <p className="mt-1 text-xs text-destructive" role="alert">{rowErrors[member.user_id]}</p>
                  ) : null}
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      size="icon"
                      variant="ghost"
                      loading={rowBusy[member.user_id]}
                      aria-label={t('memberActions', { name: member.display_name })}
                      title={tc('actions')}
                    >
                      <MoreHorizontal />
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
              </li>
            ))}
          </ul>
        ) : filtersActive ? (
          <EmptyState
            icon={<Search />}
            title={t('noFilteredMembers')}
            description={t('noFilteredMembersDescription')}
            action={<Button size="sm" variant="outline" onClick={clearFilters}>{tc('clearFilters')}</Button>}
          />
        ) : (
          <EmptyState icon={<UserRound />} title={t('noMembers')} description={t('noMembersDescription')} />
        )}
      </div>

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
