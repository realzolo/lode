'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/table';
import type { Experience } from '@/lib/types';
import { fetchExperiences } from '@/lib/api';
import { ApplicationLoader } from '../sections';

export default function AppExperiencesPage({ params }: { params: { id: string } }) {
  const t = useTranslations('experiences');
  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <ApplicationLoader id={params.id} refreshNonce={0}>
        {() => <AppExperiencesView appId={Number(params.id)} />}
      </ApplicationLoader>
    </>
  );
}

// App-scoped view of Shared Experience. Reuses the same rendering as the global
// /experiences page but filters by the current application via `application_id`.
function AppExperiencesView({ appId }: { appId: number }) {
  const t = useTranslations('experiences');
  const tc = useTranslations('common');
  const [experiences, setExperiences] = useState<Experience[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchExperiences(appId)
      .then((d) => active && setExperiences(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [appId]);

  return (
    <div style={{ marginTop: 20 }}>
      {loading && (
        <Card style={{ padding: 0, overflow: 'hidden' }} aria-busy="true">
          <div className="stack" style={{ padding: 12, gap: 12 }}>
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton className="h-3.5 w-32" />
                <Skeleton className="h-4 flex-1 max-w-[320px]" />
                <Skeleton className="h-5 w-14" />
              </div>
            ))}
          </div>
        </Card>
      )}
      {error && (
        <p className="muted" style={{ color: 'var(--danger)' }}>
          {error}
        </p>
      )}
      {!loading && !error && experiences.length === 0 && (
        <p className="muted">{tc('empty')}</p>
      )}
      {!loading && !error && experiences.length > 0 && (
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <Table>
            <THead>
              <Tr>
                <Th>{t('trigger')}</Th>
                <Th>{t('content')}</Th>
                <Th>{t('valid')}</Th>
              </Tr>
            </THead>
            <TBody>
              {experiences.map((m) => (
                <Tr key={m.id}>
                  <Td className="mono muted">{m.triggerSignature}</Td>
                  <Td>{m.content}</Td>
                  <Td>
                    <Badge variant={m.valid ? 'success' : 'danger'}>
                      {m.valid ? t('valid') : 'invalid'}
                    </Badge>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
