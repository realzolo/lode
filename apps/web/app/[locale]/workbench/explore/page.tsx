'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Select } from '@/components/ui/select';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { fetchApplications, fetchApplication, executeQuery, type QueryResult } from '@/lib/api';
import type { Application } from '@/lib/types';

interface SourceRow {
  id: number;
  name: string;
  conn_secret_ref: string;
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
  const [sql, setSql] = useState<string>('');
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
      setSql('');
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
          allowed_tables: Array.isArray(s.allowed_tables)
            ? (s.allowed_tables as unknown[]).map(String)
            : [],
        }));
        setSources(ds);
        const first = ds[0];
        setSourceId(first ? first.id : '');
        const tbl = first?.allowed_tables[0];
        setSql(tbl ? `SELECT * FROM ${tbl} LIMIT 50;` : 'SELECT 1;');
        setResult(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [appId]);

  const canRun = !!appId && sources.length > 0 && sql.trim().length > 0 && !loading;

  async function run() {
    if (!canRun) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await executeQuery(appId, {
        sql,
        source_id: sourceId === '' ? undefined : sourceId,
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
                  onChange={(e) =>
                    setSourceId(e.target.value === '' ? '' : Number(e.target.value))
                  }
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
              <div className="col" style={{ gap: 6 }}>
                <span className="muted text-sm">{t('allowedTables')}</span>
                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                  {sources
                    .find((s) => s.id === sourceId)
                    ?.allowed_tables.map((tbl) => (
                      <Badge key={tbl} variant="default">
                        {tbl}
                      </Badge>
                    )) ?? <span className="muted text-sm">—</span>}
                </div>
              </div>

              <div className="col" style={{ gap: 6 }}>
                <span className="muted text-sm">{t('sql')}</span>
                <Textarea
                  value={sql}
                  onChange={(e) => setSql(e.target.value)}
                  rows={6}
                  placeholder={t('sqlPlaceholder')}
                  className="mono"
                />
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
