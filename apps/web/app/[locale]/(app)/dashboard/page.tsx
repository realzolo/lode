'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Link } from '@/lib/navigation';
import type { Application } from '@/lib/types';
import { fetchApplications } from '@/lib/api';
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

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-[32px] font-semibold tracking-[-0.04em] leading-[1.15] text-foreground">
            {t('title')}
          </h1>
          <p className="mt-1.5 text-sm text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button variant="primary" className="shrink-0">
          <IconPlus size={16} />
          {t('newApplication')}
        </Button>
      </div>

      {loading && (
        <p className="text-sm text-muted-foreground">{tc('loading')}</p>
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
            href={`/applications/${app.id}`}
            className="group block rounded-md outline-none transition focus-visible:shadow-geist-focus"
          >
            <Card className="flex items-start gap-3.5 p-6 shadow-none group-hover:border-foreground/25 group-hover:bg-accent/40">
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
                  {app.topic}
                </div>
              </div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}