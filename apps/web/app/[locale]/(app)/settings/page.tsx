'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { fetchSettings, type GlobalSettings } from '@/lib/api';

export default function SettingsPage() {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const [settings, setSettings] = useState<GlobalSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchSettings()
      .then((d) => active && setSettings(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      {loading && <p className="muted">{tc('loading')}</p>}
      {error && <p className="muted" style={{ color: 'var(--danger, #f87171)' }}>{error}</p>}

      {settings && (
        <div className="stack" style={{ marginTop: 24 }}>
          <Card>
            <h2 className="section-title">{t('gitAccount')}</h2>
            <p className="page-subtitle">{t('gitAccountDesc')}</p>
            {settings.git_credentials.length === 0 && <p className="muted">{tc('empty')}</p>}
            {settings.git_credentials.map((c) => (
              <div key={c.id} className="row-between" style={{ marginTop: 8 }}>
                <span className="mono">{c.username || '—'}</span>
                <Badge variant={c.readonly ? 'success' : 'warning'}>
                  {c.readonly ? 'readonly' : 'rw'}
                </Badge>
              </div>
            ))}
          </Card>

          <Card>
            <h2 className="section-title">{t('repoRegistry')}</h2>
            {settings.git_repos.length === 0 && <p className="muted">{tc('empty')}</p>}
            <div className="stack">
              {settings.git_repos.map((r) => (
                <div key={r.id} className="row-between">
                  <span className="mono">{r.name}</span>
                  <span className="muted" style={{ fontSize: 12 }}>{r.repo_url}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <h2 className="section-title">{t('aiModel')}</h2>
            <p className="page-subtitle">{t('aiModelDesc')}</p>
            {settings.ai_model_configs.length === 0 && <p className="muted">{tc('empty')}</p>}
            {settings.ai_model_configs.map((m) => (
              <div key={m.id} className="row-between" style={{ marginTop: 8 }}>
                <span className="mono">
                  {m.provider} · {m.model}
                  {m.scope === 'application' ? ` (app ${m.application_id})` : ' (global)'}
                </span>
                {m.is_default && <Badge variant="accent">default</Badge>}
              </div>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
