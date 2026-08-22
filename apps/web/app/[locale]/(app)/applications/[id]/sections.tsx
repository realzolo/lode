'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchApplication } from '@/lib/api';

export type AppDetail = Awaited<ReturnType<typeof fetchApplication>>;

// Shared section renderers used by the per-section routes under
// /applications/[id]/(repos|prompts|db|model). Each route fetches the
// application once via <ApplicationLoader> and passes the detail down, so the
// left sidebar's second-level nav simply swaps which section is shown.
export function ReposSection({ data }: { data: AppDetail }) {
  const t = useTranslations('application');
  const tc = useTranslations('common');
  return (
    <Card>
      <p className="page-subtitle">{t('reposDesc')}</p>
      <div className="stack">
        {data.repos.length === 0 && <p className="muted">{tc('empty')}</p>}
        {data.repos.map((r) => (
          <div key={r.url} className="stack" style={{ gap: 4 }}>
            <div className="row-between">
              <span className="mono">{r.name}</span>
              <span className="muted" style={{ fontSize: 12 }}>{r.url}</span>
            </div>
            <p className="muted" style={{ fontSize: 13 }}>{r.description || '—'}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function PromptsSection({ data }: { data: AppDetail }) {
  const tc = useTranslations('common');
  return (
    <Card>
      <div className="stack">
        {data.preset_prompts.length === 0 && <p className="muted">{tc('empty')}</p>}
        {data.preset_prompts.map((p, i) => (
          <div key={i} className="stack" style={{ gap: 4 }}>
            <span className="field-label">{p.type}</span>
            <p style={{ whiteSpace: 'pre-wrap' }}>{p.content}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function DbSourcesSection({ data }: { data: AppDetail }) {
  const tc = useTranslations('common');
  return (
    <Card>
      <div className="stack">
        {data.db_sources.length === 0 && <p className="muted">{tc('empty')}</p>}
        {data.db_sources.map((s) => (
          <div key={s.name} className="stack" style={{ gap: 4 }}>
            <span className="field-label">{s.name}</span>
            <p className="muted mono" style={{ fontSize: 13 }}>
              allowed_tables: {Array.isArray(s.allowed_tables) ? (s.allowed_tables as unknown[]).join(', ') : '—'}
            </p>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function ModelSection() {
  return (
    <Card>
      <p className="muted" style={{ fontSize: 13 }}>
        No per-application override configured — the global default is used.
      </p>
    </Card>
  );
}

function ApplicationLoading() {
  return (
    <div aria-busy="true">
      <h1 className="page-title">
        <Skeleton className="h-8 w-48" />
      </h1>
      <Card className="stack" style={{ marginTop: 16 }}>
        <Skeleton className="h-3.5 w-24" />
        <Skeleton className="h-10 w-full max-w-[360px]" />
      </Card>
      <div style={{ marginTop: 20 }}>
        <Skeleton className="h-8 w-72" />
        <Card className="stack" style={{ marginTop: 16 }}>
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-4 w-2/3" />
        </Card>
      </div>
    </div>
  );
}

export function ApplicationLoader({
  id,
  children,
}: {
  id: string;
  children: (data: AppDetail) => ReactNode;
}) {
  const tc = useTranslations('common');
  const [data, setData] = useState<AppDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchApplication(id)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [id]);

  if (loading) return <ApplicationLoading />;
  if (error) return <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>;
  if (!data) return <p className="muted">{tc('empty')}</p>;
  return <>{children(data)}</>;
}
