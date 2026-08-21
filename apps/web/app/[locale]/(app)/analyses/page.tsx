'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/table';
import { Link } from '@/lib/navigation';
import type { Analysis, AnalysisStatus } from '@/lib/types';
import { fetchAnalyses } from '@/lib/api';

function statusVariant(status: AnalysisStatus): 'success' | 'warning' | 'danger' | 'accent' | 'default' {
  switch (status) {
    case 'completed':
      return 'success';
    case 'failed':
      return 'danger';
    case 'running':
    case 'needs_human':
      return 'warning';
    case 'pending':
    default:
      return 'accent';
  }
}

export default function AnalysesPage() {
  const t = useTranslations('analyses');
  const tc = useTranslations('common');
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchAnalyses()
      .then((data) => active && setAnalyses(data))
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
      {error && <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>}
      {!loading && !error && analyses.length === 0 && (
        <p className="muted">{tc('empty')}</p>
      )}
      <Card style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}>
        <Table>
          <THead>
            <Tr>
              <Th>{t('title')}</Th>
              <Th>{t('dedupeKey')}</Th>
              <Th>{t('level')}</Th>
              <Th>{t('status')}</Th>
              <Th />
            </Tr>
          </THead>
          <TBody>
            {analyses.map((a) => (
              <Tr key={a.dedupeKey}>
                <Td>{a.title}</Td>
                <Td className="mono muted">{a.dedupeKey}</Td>
                <Td>
                  <Badge variant={a.level === 'CRITICAL' ? 'danger' : 'warning'}>
                    {a.level}
                  </Badge>
                </Td>
                <Td>
                  <Badge variant={statusVariant(a.status)}>{a.status}</Badge>
                </Td>
                <Td className="row" style={{ justifyContent: 'flex-end' }}>
                  <Link href={`/analysis/${a.dedupeKey}`}>
                    <Badge variant="accent">{t('view')}</Badge>
                  </Link>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </Card>
    </>
  );
}
