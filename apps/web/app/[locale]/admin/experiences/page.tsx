'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/table';
import type { Experience } from '@/lib/types';
import { fetchExperiences } from '@/lib/api';

// Admin-facing view of the shared experience bank. The data is the same global
// store developers see under /workbench/experiences, but it lives in the Admin
// Console so administrators can audit triggered experiences in one place. (Admin
// mutation actions on experiences are a later milestone.)
export default function AdminExperiencesPage() {
  const t = useTranslations('experiences');
  const tc = useTranslations('common');
  const [experiences, setExperiences] = useState<Experience[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchExperiences()
      .then((data) => active && setExperiences(data))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <div className="experience-panel" aria-busy={loading}>
        {loading ? (
          <div className="experience-state experience-loading">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton className="h-3.5 w-32" />
                <Skeleton className="h-4 flex-1 max-w-[320px]" />
                <Skeleton className="h-5 w-14" />
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="experience-state">
            <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>
            <Button variant="outline" size="sm" onClick={() => setReloadKey((key) => key + 1)}>{tc('retry')}</Button>
          </div>
        ) : experiences.length === 0 ? (
          <div className="experience-state"><p className="muted">{tc('empty')}</p></div>
        ) : (
          <div className="experience-table"><Table>
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
          </Table></div>
        )}
      </div>
    </>
  );
}
