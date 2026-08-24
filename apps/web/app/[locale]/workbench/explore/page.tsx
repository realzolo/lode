'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
  const tc = useTranslations('common');
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
  const [appError, setAppError] = useState<string | null>(null);
  const [appReloadKey, setAppReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setAppLoading(true);
    setAppError(null);
    fetchApplications()
      .then((data) => active && setApps(data))
      .catch((e) => active && setAppError(String(e)))
      .finally(() => active && setAppLoading(false));
    return () => {
      active = false;
    };
  }, [appReloadKey]);

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
    <main className="query-page">
      <header className="query-page-header">
        <h1 className="page-title">{t('title')}</h1>
        <p className="muted query-page-description">{t('subtitle')}</p>
      </header>

      {appError ? (
        <div className="query-error" role="alert">
          <p className="muted" style={{ color: 'var(--red)' }}>{t('failed')}: {appError}</p>
          <Button variant="outline" size="sm" onClick={() => setAppReloadKey((key) => key + 1)}>{tc('retry')}</Button>
        </div>
      ) : <section className="query-controls">
        <div className="stack" style={{ gap: 16 }}>
          <div className="row" style={{ gap: 16, flexWrap: 'wrap' }}>
            <label className="form-field query-field" style={{ minWidth: 240, flex: 1 }}>
              <span className="muted text-sm">{t('selectApp')}</span>
              <Select
                value={appId}
                disabled={appLoading}
                placeholder={appLoading ? '…' : apps.length === 0 ? tc('empty') : t('selectAppHint')}
                onChange={(e) => setAppId(e.target.value)}
              >
                <option value="">{appLoading ? '…' : apps.length === 0 ? tc('empty') : t('selectAppHint')}</option>
                {apps.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </label>

            {sources.length > 0 && (
              <label className="form-field query-field" style={{ minWidth: 240, flex: 1 }}>
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
                <label className="form-field query-field" style={{ minWidth: 240, flex: 1 }}>
                  <span className="muted text-sm">{t('allowedTables')}</span>
                  <Select value={table} onChange={(e) => setTable(e.target.value)}>
                    {(selectedSource?.allowed_tables ?? []).map((item) => <option key={item} value={item}>{item}</option>)}
                  </Select>
                </label>
                <label className="form-field query-field" style={{ minWidth: 200, flex: 1 }}>
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
      </section>}

      {error && (
        <div className="query-error">
          <p className="muted" style={{ color: 'var(--red)' }}>
            {t('failed')}: {error}
          </p>
        </div>
      )}

      {result && (
        <section className="query-result">
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
        </section>
      )}
    </main>
  );
}
