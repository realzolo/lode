'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/table';
import type { Memory } from '@/lib/types';
import { fetchMemories } from '@/lib/api';

export default function MemoriesPage() {
  const t = useTranslations('memories');
  const tc = useTranslations('common');
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchMemories()
      .then((data) => active && setMemories(data))
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
      {!loading && !error && memories.length === 0 && (
        <p className="muted">{tc('empty')}</p>
      )}
      <Card style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}>
        <Table>
          <THead>
            <Tr>
              <Th>{t('trigger')}</Th>
              <Th>{t('content')}</Th>
              <Th>{t('valid')}</Th>
            </Tr>
          </THead>
          <TBody>
            {memories.map((m) => (
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
    </>
  );
}
