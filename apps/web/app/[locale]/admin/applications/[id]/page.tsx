'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Link } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';
import { IconCheck } from '@/components/icons';
import { setApplicationTopic } from '@/lib/api';
import { ApplicationLoader, makeRefreshDispatcher } from './sections';

export default function ApplicationOverviewPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  const tn = useTranslations('nav');
  const tAdmin = useTranslations('admin');
  const isAdmin = useUser().isAdmin;
  const [refreshNonce, setRefreshNonce] = useState(0);
  const onRefresh = makeRefreshDispatcher(setRefreshNonce);

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <ApplicationLoader id={params.id} refreshNonce={refreshNonce}>
        {(data) => (
          <>
            <Card className="stack" style={{ marginTop: 16 }}>
              <label className="field-label">{t('topic')}</label>
              {isAdmin ? (
                <TopicEditor
                  appId={params.id}
                  initial={data.topic ?? ''}
                  onSaved={onRefresh}
                />
              ) : (
                <Input
                  value={data.topic ?? ''}
                  placeholder={tAdmin('topicPlaceholder')}
                  className="grow"
                  style={{ maxWidth: 360 }}
                  readOnly
                />
              )}
            </Card>

            <div className="row" style={{ gap: 12, marginTop: 20, flexWrap: 'wrap' }}>
              <StatCard label={tn('repositories')} count={data.repos.length} href={`/admin/applications/${params.id}/repos`} />
              <StatCard label={tn('prompts')} count={data.preset_prompts.length} href={`/admin/applications/${params.id}/prompts`} />
              <StatCard label={tn('dataSources')} count={data.db_sources.length} href={`/admin/applications/${params.id}/db`} />
            </div>
          </>
        )}
      </ApplicationLoader>
    </>
  );
}

// Admin-only Kafka topic editor. Mirrors the pattern in /settings → AI model:
// type-to-edit, save commits, blank input clears the binding. ``onSaved`` is
// called after the backend confirms so the parent loader re-fetches.
function TopicEditor({
  appId,
  initial,
  onSaved,
}: {
  appId: string;
  initial: string;
  onSaved: () => void;
}) {
  const t = useTranslations('admin');
  const tc = useTranslations('common');
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // If the parent re-fetches (someone else updated, or we just saved) keep the
  // editor's local value in sync without trampling the user's in-progress edit.
  useEffect(() => {
    setValue(initial);
  }, [initial]);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await setApplicationTopic(appId, value);
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const dirty = value.trim() !== initial.trim();
  const canSave = dirty && !busy;

  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row" style={{ gap: 8 }}>
        <Input
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setSaved(false);
          }}
          placeholder={t('topicPlaceholder')}
          className="grow"
          style={{ maxWidth: 360 }}
          disabled={busy}
        />
        <Button variant="primary" onClick={save} disabled={!canSave}>
          {tc('save')}
        </Button>
      </div>
      {error && (
        <p className="muted" style={{ color: 'var(--danger)', fontSize: 13 }}>
          {error}
        </p>
      )}
      {saved && !error && (
        <p
          className="muted"
          style={{ color: 'var(--success)', fontSize: 13 }}
        >
          <span className="row" style={{ gap: 6 }}>
            <IconCheck size={14} /> {t('topicSaved')}
          </span>
        </p>
      )}
      {value.trim() === '' && initial && (
        <p className="muted" style={{ fontSize: 13 }}>
          {t('topicClearedHint')}
        </p>
      )}
    </div>
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
