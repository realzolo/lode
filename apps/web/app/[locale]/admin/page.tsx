'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { ChevronRight } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Link, useRouter } from '@/lib/navigation';
import type { Application } from '@/lib/types';
import {
  fetchApplications,
  createApplication,
  pauseApplicationIngestion,
  resumeApplicationIngestion,
  startApplicationIngestion,
} from '@/lib/api';
import { relativeTime } from '@/lib/utils';
import { IconPlus } from '@/components/icons';

// Geist avatar: a neutral grayscale tile showing the app's initial. Geist reserves
// color for status/meaning, never decoration — so the tile stays monochrome and the
// level badge (red/amber) carries the status color.
function AppAvatar({ name }: { name: string }) {
  return (
    <div
      aria-hidden="true"
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-[var(--color-4)] bg-[var(--color-2)] text-[14px] font-semibold leading-none text-[var(--color-10)]"
    >
      {name.charAt(0).toUpperCase()}
    </div>
  );
}

export default function DashboardPage() {
  const t = useTranslations('dashboard');
  const tc = useTranslations('common');
  const locale = useLocale();
  const [apps, setApps] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const router = useRouter();
  const [newOpen, setNewOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [startTarget, setStartTarget] = useState<Application | null>(null);
  const [startPosition, setStartPosition] = useState<'latest' | 'earliest'>('latest');
  const [actionAppId, setActionAppId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Polling may overlap with a lifecycle mutation. A response that started
  // before the mutation must not replace the backend-confirmed local state.
  const applicationsRequestRef = useRef(0);

  const loadApplications = useCallback(async (showLoading = false) => {
    const requestId = ++applicationsRequestRef.current;
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const nextApps = await fetchApplications();
      if (requestId === applicationsRequestRef.current) setApps(nextApps);
    } catch (e) {
      if (requestId === applicationsRequestRef.current) setError(String(e));
    } finally {
      if (showLoading && requestId === applicationsRequestRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadApplications(true);
  }, [loadApplications]);

  const shouldPollIngestion = apps.some((app) => app.ingestionState === 'active');
  useEffect(() => {
    if (!shouldPollIngestion) return;
    const timer = window.setInterval(() => void loadApplications(), 5000);
    return () => window.clearInterval(timer);
  }, [loadApplications, shouldPollIngestion]);

  async function handleCreate() {
    const name = newName.trim();
    if (!name || submitting) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await createApplication({ name });
      applicationsRequestRef.current += 1;
      setApps((prev) => [created, ...prev]);
      setNewOpen(false);
      setNewName('');
      router.push(`/admin/applications/${created.id}`);
    } catch (e) {
      setFormError(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  function updateIngestion(
    appId: string,
    ingestionState: Application['ingestionState'],
    ingestionObservedState: Application['ingestionObservedState'],
    ingestionStartPosition: Application['ingestionStartPosition'],
  ) {
    applicationsRequestRef.current += 1;
    setApps((previous) => previous.map((app) => (
      app.id === appId
        ? { ...app, ingestionState, ingestionObservedState, ingestionStartPosition }
        : app
    )));
  }

  async function handleStart() {
    if (!startTarget) return;
    setActionAppId(startTarget.id);
    setActionError(null);
    try {
      const status = await startApplicationIngestion(startTarget.id, startPosition);
      updateIngestion(
        startTarget.id,
        status.desired_state,
        status.observed_state,
        status.start_position,
      );
      setStartTarget(null);
    } catch (e) {
      setActionError(String(e));
    } finally {
      setActionAppId(null);
    }
  }

  async function handleLifecycle(app: Application, action: 'pause' | 'resume') {
    setActionAppId(app.id);
    setActionError(null);
    try {
      const status = action === 'pause'
        ? await pauseApplicationIngestion(app.id)
        : await resumeApplicationIngestion(app.id);
      updateIngestion(
        app.id,
        status.desired_state,
        status.observed_state,
        status.start_position,
      );
    } catch (e) {
      setActionError(String(e));
    } finally {
      setActionAppId(null);
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-[32px] font-semibold tracking-normal leading-[1.15] text-foreground">
            {t('title')}
          </h1>
          <p className="mt-1.5 text-sm text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button variant="primary" className="shrink-0" onClick={() => setNewOpen(true)}>
          <IconPlus size={16} />
          {t('newApplication')}
        </Button>
      </div>

      {loading && apps.length === 0 && (
        <div
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
          aria-busy="true"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Card
              key={i}
              className="flex h-full flex-col gap-3.5 p-6 shadow-none"
            >
              <div className="flex items-start gap-3.5">
                <Skeleton variant="rounded" className="h-10 w-10 shrink-0" />
                <div className="min-w-0 flex-1 space-y-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-5 w-12" />
                  </div>
                  <Skeleton className="h-3.5 w-full max-w-[200px]" />
                </div>
              </div>
              <div className="mt-auto space-y-2.5 border-t border-[var(--color-4)] pt-3.5">
                <Skeleton className="h-3.5 w-40" />
              </div>
            </Card>
          ))}
        </div>
      )}
      {error && (
        <div className="dashboard-error" role="alert">
          <p className="text-sm text-destructive">{error}</p>
          <Button variant="outline" size="sm" onClick={() => void loadApplications(true)}>
            {tc('retry')}
          </Button>
        </div>
      )}
      {actionError && (
        <p className="text-sm text-destructive">{actionError}</p>
      )}
      {!loading && !error && apps.length === 0 && (
        <p className="text-sm text-muted-foreground">{tc('empty')}</p>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {apps.map((app) => {
          const needsFirstStart = app.ingestionState === 'draft'
            || (app.ingestionState === 'paused' && !app.ingestionStartPosition);
          const canManage = app.myPerm === 'admin';
          const stateVariant = app.ingestionObservedState === 'listening'
            ? 'success'
            : app.ingestionObservedState === 'paused'
              ? 'warning'
              : app.ingestionObservedState === 'error'
                ? 'danger'
              : 'default';

          return (
            <Card key={app.id} className="flex h-full flex-col gap-3.5 p-6 shadow-none">
              <Link
                href={`/admin/applications/${app.id}`}
                className="group block rounded-md outline-none transition focus-visible:shadow-geist-focus"
              >
              <div className="flex items-start gap-3.5">
                <AppAvatar name={app.name} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[16px] font-semibold leading-none text-foreground">
                      {app.name}
                    </span>
                    <Badge
                      variant={app.level === 'CRITICAL' ? 'danger' : 'warning'}
                      className="shrink-0"
                    >
                      {app.level}
                    </Badge>
                  </div>
                  <div className="mono mt-2 truncate text-[13px] text-muted-foreground">
                    {app.topic || '—'}
                  </div>
                </div>
              </div>
              <div className="mt-3.5 flex items-center gap-1.5 text-[13px] text-muted-foreground">
                <span>
                  {app.repoCount} {app.repoCount === 1 ? 'repo' : 'repos'}
                </span>
                <span aria-hidden="true" className="text-[var(--color-6)]">
                  ·
                </span>
                <span>Created {relativeTime(app.createdAt, locale)}</span>
                <ChevronRight className="ml-auto h-4 w-4 shrink-0 text-muted-foreground opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100" />
              </div>
              </Link>
              <div className="mt-auto flex items-center justify-between gap-3 border-t border-[var(--color-4)] pt-3.5">
                <Badge variant={stateVariant}>
                  {t(`ingestionObserved.${app.ingestionObservedState}`)}
                </Badge>
                {canManage && needsFirstStart && (
                  app.topic ? (
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => {
                        setActionError(null);
                        setStartPosition('latest');
                        setStartTarget(app);
                      }}
                      disabled={actionAppId === app.id}
                    >
                      {t('startIngestion')}
                    </Button>
                  ) : (
                    <Button asChild variant="secondary" size="sm">
                      <Link href={`/admin/applications/${app.id}`}>{t('configureTopic')}</Link>
                    </Button>
                  )
                )}
                {canManage && app.ingestionState === 'active' && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => void handleLifecycle(app, 'pause')}
                    disabled={actionAppId === app.id}
                  >
                    {t('pauseIngestion')}
                  </Button>
                )}
                {canManage && app.ingestionState === 'paused' && !needsFirstStart && (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => void handleLifecycle(app, 'resume')}
                    disabled={actionAppId === app.id}
                  >
                    {t('resumeIngestion')}
                  </Button>
                )}
              </div>
            </Card>
          );
        })}
      </div>

      <Dialog
        open={startTarget !== null}
        onOpenChange={(open) => {
          if (!open && !actionAppId) setStartTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('startIngestion')}</DialogTitle>
            <DialogDescription>
              {t('startIngestionDesc', { name: startTarget?.name ?? '' })}
            </DialogDescription>
          </DialogHeader>
          <label className="form-field text-sm font-medium text-foreground">
            {t('startPosition')}
            <Select
              value={startPosition}
              onChange={(event) => setStartPosition(event.target.value as 'latest' | 'earliest')}
              disabled={actionAppId !== null}
              aria-label={t('startPosition')}
            >
              <option value="latest">{t('startLatest')}</option>
              <option value="earliest">{t('startEarliest')}</option>
            </Select>
          </label>
          <DialogFooter>
            <Button variant="default" onClick={() => setStartTarget(null)} disabled={actionAppId !== null}>
              {tc('cancel')}
            </Button>
            <Button variant="primary" onClick={() => void handleStart()} disabled={actionAppId !== null}>
              {t('startIngestion')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={newOpen}
        onOpenChange={(o) => {
          setNewOpen(o);
          if (!o) {
            setNewName('');
            setFormError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('newApplication')}</DialogTitle>
            <DialogDescription>{t('subtitle')}</DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={newName}
            placeholder={t('namePlaceholder')}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreate();
            }}
            disabled={submitting}
          />
          {formError && (
            <p className="text-sm text-destructive">{formError}</p>
          )}
          <DialogFooter>
            <Button
              variant="default"
              onClick={() => setNewOpen(false)}
              disabled={submitting}
            >
              {tc('cancel')}
            </Button>
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={submitting || !newName.trim()}
            >
              {t('newApplication')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
