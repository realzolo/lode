'use client';

import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Link } from '@/lib/navigation';
import { ApplicationLoader } from './sections';

export default function ApplicationOverviewPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  const tn = useTranslations('nav');

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <ApplicationLoader id={params.id}>
        {(data) => (
          <>
            <Card className="stack" style={{ marginTop: 16 }}>
              <label className="field-label">{t('topic')}</label>
              <Input
                defaultValue={data.topic ?? ''}
                placeholder="—"
                className="grow"
                style={{ maxWidth: 360 }}
                readOnly
              />
            </Card>

            <div className="row" style={{ gap: 12, marginTop: 20, flexWrap: 'wrap' }}>
              <StatCard label={tn('repositories')} count={data.repos.length} href={`/applications/${params.id}/repos`} />
              <StatCard label={tn('prompts')} count={data.preset_prompts.length} href={`/applications/${params.id}/prompts`} />
              <StatCard label={tn('dataSources')} count={data.db_sources.length} href={`/applications/${params.id}/db`} />
            </div>
          </>
        )}
      </ApplicationLoader>
    </>
  );
}

function StatCard({ label, count, href }: { label: string; count: number; href: string }) {
  return (
    <Link
      href={href}
      className="flex w-44 flex-col gap-1 rounded-md border border-border bg-card p-5 text-card-foreground transition hover:border-foreground/25 hover:bg-accent/40 focus-visible:shadow-geist-focus"
    >
      <span className="text-2xl font-semibold tracking-[-0.02em]">{count}</span>
      <span className="text-sm text-muted-foreground">{label}</span>
    </Link>
  );
}
