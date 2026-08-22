'use client';

import { useEffect, useState } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { ChevronRight } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
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
import { fetchApplications, createApplication } from '@/lib/api';
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

  useEffect(() => {
    let active = true;
    fetchApplications()
      .then((data) => active && setApps(data))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  const router = useRouter();
  const [newOpen, setNewOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function handleCreate() {
    const name = newName.trim();
    if (!name || submitting) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await createApplication({ name });
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

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-[32px] font-semibold tracking-[-0.04em] leading-[1.15] text-foreground">
            {t('title')}
          </h1>
          <p className="mt-1.5 text-sm text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button variant="primary" className="shrink-0" onClick={() => setNewOpen(true)}>
          <IconPlus size={16} />
          {t('newApplication')}
        </Button>
      </div>

      {loading && (
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
        <p className="text-sm text-destructive">{error}</p>
      )}
      {!loading && !error && apps.length === 0 && (
        <p className="text-sm text-muted-foreground">{tc('empty')}</p>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {apps.map((app) => (
          <Link
            key={app.id}
            href={`/admin/applications/${app.id}`}
            className="group block rounded-md outline-none transition focus-visible:shadow-geist-focus"
          >
            <Card className="flex h-full flex-col gap-3.5 p-6 shadow-none transition group-hover:border-foreground/25 group-hover:bg-accent/40">
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
              <div className="mt-auto flex items-center gap-1.5 border-t border-[var(--color-4)] pt-3.5 text-[13px] text-muted-foreground">
                <span>
                  {app.repoCount} {app.repoCount === 1 ? 'repo' : 'repos'}
                </span>
                <span aria-hidden="true" className="text-[var(--color-6)]">
                  ·
                </span>
                <span>Created {relativeTime(app.createdAt, locale)}</span>
                <ChevronRight className="ml-auto h-4 w-4 shrink-0 text-muted-foreground opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100" />
              </div>
            </Card>
          </Link>
        ))}
      </div>

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