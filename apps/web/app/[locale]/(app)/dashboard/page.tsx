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
    <>
      <div className="row-between">
        <div>
          <h1 className="page-title">{t('title')}</h1>
          <p className="page-subtitle">{t('subtitle')}</p>
        </div>
        <Button variant="primary">
          <IconPlus size={16} /> {t('newApplication')}
        </Button>
      </div>

      {loading && <p className="muted">{tc('loading')}</p>}
      {error && <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>}
      {!loading && !error && apps.length === 0 && (
        <p className="muted">{tc('empty')}</p>
      )}

      <div className="app-grid">
        {apps.map((app) => (
          <Link key={app.id} href={`/applications/${app.id}`}>
            <Card className="app-card">
              <div className="row-between">
                <span className="app-name">{app.name}</span>
                <Badge variant={app.level === 'CRITICAL' ? 'danger' : 'warning'}>
                  {app.level}
                </Badge>
              </div>
              <div className="muted mono">{app.topic}</div>
            </Card>
          </Link>
        ))}
      </div>
    </>
  );
}
