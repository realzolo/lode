'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Select } from '@/components/ui/select';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/table';
import { fetchApplications, fetchApplication, executeQuery, type QueryResult } from '@/lib/api';
import type { Application } from '@/lib/types';

interface SourceRow {
  id: number;
  name: string;
  conn_secret_ref: string | null;
  host: string | null;
  port: number | null;
  database: string | null;
  username: string | null;
  has_password: boolean;
  allowed_tables: string[];
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return '∅';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export default function ExplorePage() {
  const t = useTranslations('query');
  const [apps, setApps] = useState<Application[]>([]);
  const [appId, setAppId] = useState<string>('');
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [sourceId, setSourceId] = useState<number | ''>('');
  const [table, setTable] = useState<string>('');
  const [operation, setOperation] = useState<'sample' | 'count'>('sample');
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [appLoading, setAppLoading] = useState(true);

  useEffect(() => {
    fetchApplications()
      .then(setApps)
      .catch(() => setApps([]))
      .finally(() => setAppLoading(false));
  }, []);

  useEffect(() => {
    if (!appId) {
      setSources([]);
      setSourceId('');
      setTable('');
      setResult(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    fetchApplication(appId)
      .then((data) => {
        const ds: SourceRow[] = (data.db_sources || []).map((s) => ({
          id: s.id,
          name: s.name,
          conn_secret_ref: s.conn_secret_ref,
          host: s.host,
          port: s.port,
          database: s.database,
          username: s.username,
          has_password: s.has_password,
          allowed_tables: Array.isArray(s.allowed_tables)
            ? (s.allowed_tables as unknown[]).map(String)
            : [],
        }));
        setSources(ds);
        const first = ds[0];
        setSourceId(first ? first.id : '');
        setTable(first?.allowed_tables[0] ?? '');
        setResult(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [appId]);

  const selectedSource = sources.find((source) => source.id === sourceId);
  const canRun = !!appId && !!selectedSource && !!table && !loading;

  async function run() {
    if (!canRun || sourceId === '') return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await executeQuery(appId, {
        source_id: sourceId,
        table,
        operation,
      });
      setResult(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16, maxWidth: 760 }}>
        {t('subtitle')}
      </p>

      <Card style={{ padding: 20, marginBottom: 16 }}>
        <div className="stack" style={{ gap: 16 }}>
          <div className="row" style={{ gap: 16, flexWrap: 'wrap' }}>
            <label className="col" style={{ gap: 6, minWidth: 240, flex: 1 }}>
              <span className="muted text-sm">{t('selectApp')}</span>
              <Select
                value={appId}
                disabled={appLoading}
                onChange={(e) => setAppId(e.target.value)}
              >
                <option value="">{appLoading ? '…' : t('selectAppHint')}</option>
                {apps.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </label>

            {sources.length > 0 && (
              <label className="col" style={{ gap: 6, minWidth: 240, flex: 1 }}>
                <span className="muted text-sm">{t('dataSource')}</span>
                <Select
                  value={sourceId === '' ? '' : String(sourceId)}
                  onChange={(e) => {
                    const nextId = e.target.value === '' ? '' : Number(e.target.value);
                    setSourceId(nextId);
                    const next = sources.find((source) => source.id === nextId);
                    setTable(next?.allowed_tables[0] ?? '');
                  }}
                >
                  {sources.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.allowed_tables.length} tables)
                    </option>
                  ))}
                </Select>
              </label>
            )}
          </div>

          {appId && sources.length === 0 && !loading && (
            <p className="muted" style={{ color: 'var(--amber)' }}>
              {t('noSources')}
            </p>
          )}

          {sources.length > 0 && (
            <>
              <div className="row" style={{ gap: 16, flexWrap: 'wrap' }}>
                <label className="col" style={{ gap: 6, minWidth: 240, flex: 1 }}>
                  <span className="muted text-sm">{t('allowedTables')}</span>
                  <Select value={table} onChange={(e) => setTable(e.target.value)}>
                    {(selectedSource?.allowed_tables ?? []).map((item) => <option key={item} value={item}>{item}</option>)}
                  </Select>
                </label>
                <label className="col" style={{ gap: 6, minWidth: 200, flex: 1 }}>
                  <span className="muted text-sm">查询操作</span>
                  <Select value={operation} onChange={(e) => setOperation(e.target.value as 'sample' | 'count')}>
                    <option value="sample">脱敏样本（最多 100 行）</option>
                    <option value="count">行数统计</option>
                  </Select>
                </label>
              </div>

              <div className="row" style={{ justifyContent: 'flex-end' }}>
                <Button variant="primary" disabled={!canRun} onClick={run}>
                  {loading ? t('running') : t('run')}
                </Button>
              </div>
            </>
          )}
        </div>
      </Card>

      {error && (
        <Card style={{ padding: 16, marginBottom: 16, borderColor: 'var(--red)' }}>
          <p className="muted" style={{ color: 'var(--red)' }}>
            {t('failed')}: {error}
          </p>
        </Card>
      )}

      {result && (
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <div
            className="row"
            style={{ gap: 10, padding: '12px 16px', borderBottom: '1px solid var(--border)' }}
          >
            <Badge variant="accent">{result.row_count} {t('rows')}</Badge>
            <Badge variant="default">{result.source_name ?? 'source'}</Badge>
            {result.truncated && <Badge variant="warning">{t('truncated')}</Badge>}
            {result.desensitized && <Badge variant="success">{t('desensitized')}</Badge>}
          </div>
          {result.columns.length === 0 || result.rows.length === 0 ? (
            <p className="muted" style={{ padding: 16 }}>
              {t('empty')}
            </p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <Table>
                <THead>
                  <Tr>
                    {result.columns.map((c) => (
                      <Th key={c}>{c}</Th>
                    ))}
                  </Tr>
                </THead>
                <TBody>
                  {result.rows.map((row, i) => (
                    <Tr key={i}>
                      {result.columns.map((c) => (
                        <Td key={c} className="mono">
                          {renderCell(row[c])}
                        </Td>
                      ))}
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
