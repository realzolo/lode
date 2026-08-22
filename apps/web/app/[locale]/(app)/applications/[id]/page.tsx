'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select } from '@/components/ui/select';
import { Tabs } from '@/components/ui/tabs';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchApplication } from '@/lib/api';

export default function ApplicationPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  const tc = useTranslations('common');
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchApplication>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchApplication(params.id)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [params.id]);

  if (loading)
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
  if (error) return <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>;
  if (!data) return <p className="muted">{tc('empty')}</p>;

  const reposTab = (
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

  const promptsTab = (
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

  const dbSourcesTab = (
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

  const modelTab = (
    <Card>
      <Select defaultValue="" className="grow" style={{ maxWidth: 320 }}>
        <option value="" disabled>
          {tc('appName')}…
        </option>
        <option value="openai">OpenAI</option>
        <option value="anthropic">Anthropic</option>
      </Select>
      <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
        No per-application override configured — the global default is used.
      </p>
    </Card>
  );

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <Card className="stack" style={{ marginTop: 16 }}>
        <label className="field-label">{t('topic')}</label>
        <Input defaultValue={data.topic ?? `alert.${params.id}`} className="grow" style={{ maxWidth: 360 }} readOnly />
      </Card>

      <div style={{ marginTop: 20 }}>
        <Tabs
          items={[
            { value: 'repos', label: t('repos'), content: reposTab },
            { value: 'prompts', label: t('prompts'), content: promptsTab },
            { value: 'db', label: t('dbSources'), content: dbSourcesTab },
            { value: 'model', label: t('modelOverride'), content: modelTab },
          ]}
        />
      </div>
    </>
  );
}
