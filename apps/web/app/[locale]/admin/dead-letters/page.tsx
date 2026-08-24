'use client';

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Table,
  THead,
  TBody,
  Tr,
  Th,
  Td,
} from '@/components/ui/table';
import type { DeadLetter } from '@/lib/types';
import { fetchDeadLetters, replayDeadLetter } from '@/lib/api';

type KindFilter = 'all' | 'dlq' | 'unassigned';

// Admin console for the dead-letter queue. The consumer routes rejected messages
// here (parse failures, schema errors, or topics with no mapped application);
// this page lets operators inspect them and re-inject a message onto its source
// Kafka topic so the consumer re-processes it. Admin-gated by the parent layout.
export default function AdminDeadLettersPage() {
  const t = useTranslations('deadLetters');
  const tc = useTranslations('common');

  const [items, setItems] = useState<DeadLetter[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<KindFilter>('all');
  const [replayingId, setReplayingId] = useState<number | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [replayTarget, setReplayTarget] = useState<DeadLetter | null>(null);
  const requestRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchDeadLetters(
        kind === 'all' ? undefined : (kind as 'dlq' | 'unassigned'),
      );
      if (requestId === requestRef.current) setItems(data);
    } catch (e) {
      if (requestId === requestRef.current) setError(String(e));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [kind]);

  useEffect(() => {
    load();
  }, [load]);

  const onReplay = async () => {
    if (!replayTarget) return;
    setReplayingId(replayTarget.id);
    setReplayError(null);
    try {
      await replayDeadLetter(replayTarget.id);
      // Reflect the replay locally without a refetch flash.
      setItems((prev) =>
        prev.map((d) => (d.id === replayTarget.id ? { ...d, replayed: true } : d)),
      );
    } catch (e) {
      setReplayError(String(e));
      throw e;
    } finally {
      setReplayingId(null);
    }
  };

  const fmtTime = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
  };

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <p className="muted" style={{ marginTop: 4 }}>
        {t('subtitle')}
      </p>

      <Card style={{ padding: 16, marginTop: 16 }}>
        <div className="row" style={{ gap: 12, alignItems: 'flex-end' }}>
          <label style={labelStyle}>
            <span>{t('kind')}</span>
            <Select
              value={kind}
              onChange={(e) => setKind(e.target.value as KindFilter)}
            >
              <option value="all">{t('all')}</option>
              <option value="dlq">{t('dlq')}</option>
              <option value="unassigned">{t('unassigned')}</option>
            </Select>
          </label>
        </div>
      </Card>

      {replayError && (
        <p className="muted" style={{ color: 'var(--danger)', marginTop: 16 }}>
          {replayError}
        </p>
      )}
      {error && (
        <div className="dashboard-error" role="alert">
          <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>
          <Button variant="outline" size="sm" onClick={() => void load()}>{tc('retry')}</Button>
        </div>
      )}

      {loading && items.length === 0 && (
        <Card
          style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}
          aria-busy="true"
        >
          <div className="stack" style={{ padding: 12, gap: 12 }}>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton className="h-3.5 w-40" />
                <Skeleton className="h-4 flex-1 max-w-[280px]" />
                <Skeleton className="h-5 w-14" />
              </div>
            ))}
          </div>
        </Card>
      )}

      {!loading && !error && items.length === 0 && (
        <p className="muted" style={{ marginTop: 16 }}>
          {tc('empty')}
        </p>
      )}

      {items.length > 0 && (
        <div className="operational-table">
          <Table>
            <THead>
              <Tr>
                <Th>{t('time')}</Th>
                <Th>{t('kind')}</Th>
                <Th>{t('topic')}</Th>
                <Th>{t('dedupeKey')}</Th>
                <Th>{t('reason')}</Th>
                <Th>{t('payload')}</Th>
                <Th>{t('status')}</Th>
                <Th>{t('actions')}</Th>
              </Tr>
            </THead>
            <TBody>
              {items.map((d) => (
                <Tr key={d.id}>
                  <Td className="mono muted">{fmtTime(d.created_at)}</Td>
                  <Td>
                    <Badge
                      variant={
                        d.kind === 'unassigned' ? 'warning' : 'danger'
                      }
                    >
                      {d.kind}
                    </Badge>
                  </Td>
                  <Td className="mono">{d.topic}</Td>
                  <Td className="mono muted">{d.dedupe_key ?? '—'}</Td>
                  <Td className="muted">{d.reason ?? '—'}</Td>
                  <Td
                    className="mono muted"
                    title={JSON.stringify(d.payload ?? {})}
                  >
                    {d.payload ? JSON.stringify(d.payload) : '—'}
                  </Td>
                  <Td>
                    {d.replayed ? (
                      <Badge variant="success">{t('replayed')}</Badge>
                    ) : (
                      <Badge variant="default">{t('pending')}</Badge>
                    )}
                  </Td>
                  <Td>
                    {d.replayed ? (
                      <span className="muted">—</span>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setReplayTarget(d)}
                        disabled={replayingId === d.id}
                      >
                        {t('replay')}
                      </Button>
                    )}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      )}
      <ConfirmDialog
        open={replayTarget !== null}
        onOpenChange={(open) => !open && setReplayTarget(null)}
        title={t('replayTitle')}
        description={replayTarget ? t('replayDescription', { topic: replayTarget.topic }) : undefined}
        confirmLabel={t('replay')}
        cancelLabel={tc('cancel')}
        successMessage={t('replaySucceeded')}
        onConfirm={onReplay}
      />
    </>
  );
}

const labelStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
  fontSize: 13,
  minWidth: 200,
};
