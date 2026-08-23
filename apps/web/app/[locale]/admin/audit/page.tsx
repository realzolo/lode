'use client';

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  THead,
  TBody,
  Tr,
  Th,
  Td,
} from '@/components/ui/table';
import type { AuditEvent, AuditEventList } from '@/lib/types';
import { fetchAuditEvents } from '@/lib/api';

const PAGE_SIZE = 50;

type ResultFilter = 'all' | 'ok' | 'error';

// Admin-facing read view of the append-only audit trail. The data is written by
// every privileged control-plane mutation (application / user / invite / auth /
// settings / query / dlq). This page makes it observable: filter, read, and
// page through it. It is admin-gated by the parent layout.
export default function AdminAuditPage() {
  const t = useTranslations('audit');
  const tc = useTranslations('common');

  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  // Committed query (what the table is currently showing).
  const [q, setQ] = useState<{
    action: string;
    application_id: string;
    result: ResultFilter;
  }>({ action: '', application_id: '', result: 'all' });

  // Local form state for the filter inputs.
  const [formAction, setFormAction] = useState('');
  const [formApp, setFormApp] = useState('');
  const [formResult, setFormResult] = useState<ResultFilter>('all');

  // Where the next "load more" page starts. A ref (not state) so the effect and
  // the button share one source of truth without re-triggering the effect.
  const offsetRef = useRef(0);

  const runQuery = useCallback(
    async (reset: boolean) => {
      const nextOffset = reset ? 0 : offsetRef.current;
      setLoading(true);
      setError(null);
      try {
        const res: AuditEventList = await fetchAuditEvents({
          action: q.action.trim() || undefined,
          application_id: q.application_id.trim()
            ? Number(q.application_id)
            : undefined,
          result: q.result === 'all' ? undefined : q.result,
          limit: PAGE_SIZE,
          offset: nextOffset,
        });
        setTotal(res.total);
        setEvents((prev) => (reset ? res.items : [...prev, ...res.items]));
        offsetRef.current = res.offset + res.items.length;
        setHasMore(res.offset + res.items.length < res.total);
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    },
    [q],
  );

  // Re-run from the top whenever the committed query changes (after Search).
  useEffect(() => {
    offsetRef.current = 0;
    runQuery(true);
  }, [runQuery]);

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    setQ({
      action: formAction,
      application_id: formApp,
      result: formResult,
    });
  };

  const fmtTime = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
  };

  const actorLabel = (ev: AuditEvent) =>
    ev.actor_email ?? (ev.actor_id != null ? `#${ev.actor_id}` : '—');

  const targetLabel = (ev: AuditEvent) =>
    ev.target_type != null
      ? `${ev.target_type}:${ev.target_id ?? ''}`
      : '—';

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <p className="muted" style={{ marginTop: 4 }}>
        {t('subtitle')}
      </p>

      <Card style={{ padding: 16, marginTop: 16 }}>
        <form
          className="row"
          style={{ gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}
          onSubmit={onSearch}
        >
          <label style={labelStyle}>
            <span>{t('action')}</span>
            <Input
              value={formAction}
              onChange={(e) => setFormAction(e.target.value)}
              placeholder={t('filterAction')}
            />
          </label>
          <label style={labelStyle}>
            <span>{t('target')}</span>
            <Input
              value={formApp}
              onChange={(e) => setFormApp(e.target.value)}
              placeholder={t('filterApp')}
              inputMode="numeric"
            />
          </label>
          <label style={labelStyle}>
            <span>{t('result')}</span>
            <Select
              value={formResult}
              onChange={(e) => setFormResult(e.target.value as ResultFilter)}
            >
              <option value="all">{t('all')}</option>
              <option value="ok">{t('ok')}</option>
              <option value="error">{t('error')}</option>
            </Select>
          </label>
          <Button type="submit" variant="primary" disabled={loading}>
            {t('search')}
          </Button>
        </form>
      </Card>

      {error && (
        <p className="muted" style={{ color: 'var(--danger)', marginTop: 16 }}>
          {error}
        </p>
      )}

      {loading && events.length === 0 && (
        <Card
          style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}
          aria-busy="true"
        >
          <div className="stack" style={{ padding: 12, gap: 12 }}>
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton className="h-3.5 w-40" />
                <Skeleton className="h-4 flex-1 max-w-[280px]" />
                <Skeleton className="h-5 w-14" />
              </div>
            ))}
          </div>
        </Card>
      )}

      {!loading && !error && events.length === 0 && (
        <p className="muted" style={{ marginTop: 16 }}>
          {tc('empty')}
        </p>
      )}

      {events.length > 0 && (
        <Card style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}>
          <Table>
            <THead>
              <Tr>
                <Th>{t('time')}</Th>
                <Th>{t('action')}</Th>
                <Th>{t('actor')}</Th>
                <Th>{t('target')}</Th>
                <Th>{t('result')}</Th>
                <Th>{t('detail')}</Th>
              </Tr>
            </THead>
            <TBody>
              {events.map((ev) => (
                <Tr key={ev.id}>
                  <Td className="mono muted">{fmtTime(ev.created_at)}</Td>
                  <Td className="mono">{ev.action}</Td>
                  <Td>{actorLabel(ev)}</Td>
                  <Td className="mono muted">{targetLabel(ev)}</Td>
                  <Td>
                    <Badge variant={ev.result === 'ok' ? 'success' : 'danger'}>
                      {ev.result === 'ok' ? t('ok') : t('error')}
                    </Badge>
                  </Td>
                  <Td
                    className="mono muted"
                    title={JSON.stringify(ev.detail ?? {})}
                  >
                    {ev.detail ? JSON.stringify(ev.detail) : '—'}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </Card>
      )}

      <div
        className="row"
        style={{ marginTop: 12, gap: 12, justifyContent: 'space-between' }}
      >
        {events.length > 0 && (
          <span className="muted">
            {t('showing', { shown: events.length, total })}
          </span>
        )}
        {hasMore && (
          <Button
            variant="outline"
            onClick={() => runQuery(false)}
            disabled={loading}
          >
            {t('loadMore')}
          </Button>
        )}
      </div>
    </>
  );
}

const labelStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  fontSize: 13,
  minWidth: 200,
};
