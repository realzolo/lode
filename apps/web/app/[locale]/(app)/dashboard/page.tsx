'use client';

import { useEffect, useMemo, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Link } from '@/lib/navigation';
import type { Application } from '@/lib/types';
import { fetchApplications } from '@/lib/api';
import { IconPlus } from '@/components/icons';

// Vercel-style project avatar: small gradient tile with the first letter of the
// app name. The color is hash-stable so the same app always shows the same tile.
const APP_GRADIENTS = [
  'from-[#0070f3] to-[#3291ff]',
  'from-[#ff4d4f] to-[#ff7a45]',
  'from-[#00c389] to-[#3ed598]',
  'from-[#ffae00] to-[#ffcd3a]',
  'from-[#8b5cf6] to-[#a78bfa]',
  'from-[#ec4899] to-[#f472b6]',
  'from-[#06b6d4] to-[#22d3ee]',
  'from-[#f97316] to-[#fb923c]',
];

function hashName(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  return h;
}

function AppAvatar({ name }: { name: string }) {
  const gradient = useMemo(
    () => APP_GRADIENTS[hashName(name) % APP_GRADIENTS.length],
    [name],
  );
  return (
    <div
      aria-hidden="true"
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-gradient-to-br ${gradient} text-[15px] font-semibold leading-none text-white shadow-sm`}
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
          <h1 className="text-3xl font-semibold tracking-tight text-foreground">
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
            className="group block rounded-xl outline-none transition focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <Card className="flex items-start gap-3.5 p-5 shadow-none transition-colors group-hover:border-foreground/25 group-hover:bg-accent/40">
              <AppAvatar name={app.name} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[15px] font-medium leading-none text-foreground">
                    {app.name}
                  </span>
                  <Badge
                    variant={app.level === 'CRITICAL' ? 'danger' : 'warning'}
                    className="shrink-0"
                  >
                    {app.level}
                  </Badge>
                </div>
                <div className="mono mt-2 truncate text-xs text-muted-foreground">
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