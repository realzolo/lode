'use client';

import { useEffect, useMemo, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Skeleton } from '@/components/ui/skeleton';
import { Link } from '@/lib/navigation';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { IconArrowUpRight, IconSearch } from '@/components/icons';
import type { Analysis, AnalysisStatus } from '@/lib/types';
import { fetchAnalyses } from '@/lib/api';

function statusVariant(status: AnalysisStatus): 'success' | 'warning' | 'danger' | 'accent' | 'default' {
  switch (status) {
    case 'completed':
      return 'success';
    case 'failed':
      return 'danger';
    case 'running':
    case 'needs_review':
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
  const tn = useTranslations('nav');
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [levelFilter, setLevelFilter] = useState('all');
  const [applicationFilter, setApplicationFilter] = useState('all');
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchAnalyses()
      .then((data) => active && setAnalyses(data))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  const filteredAnalyses = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return analyses.filter((analysis) => {
      const matchesQuery = `${analysis.title} ${analysis.dedupeKey} ${analysis.status} ${analysis.level} ${analysis.applicationName}`.toLowerCase().includes(normalized);
      const matchesStatus = statusFilter === 'all' || analysis.status === statusFilter;
      const matchesLevel = levelFilter === 'all' || analysis.level === levelFilter;
      const matchesApplication = applicationFilter === 'all' || analysis.applicationId === applicationFilter;
      return matchesQuery && matchesStatus && matchesLevel && matchesApplication;
    });
  }, [analyses, applicationFilter, levelFilter, query, statusFilter]);

  const applications = useMemo(() => [...new Map(analyses.map((analysis) => [analysis.applicationId, analysis.applicationName])).entries()], [analyses]);
  const hasActiveFilters = Boolean(query || statusFilter !== 'all' || levelFilter !== 'all' || applicationFilter !== 'all');
  const clearFilters = () => {
    setQuery('');
    setStatusFilter('all');
    setLevelFilter('all');
    setApplicationFilter('all');
  };

  return (
    <div className="dashboard-page">
      <div className="dashboard-filterbar" aria-label={t('title')}>
        <label className="dashboard-search"><IconSearch size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tc('search')} aria-label={tc('search')} /></label>
        <Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label={t('status')} className="dashboard-filter-select">
          <option value="all">{t('status')}</option><option value="pending">pending</option><option value="running">running</option><option value="completed">completed</option><option value="needs_review">needs_review</option><option value="failed">failed</option>
        </Select>
        <Select value={levelFilter} onChange={(event) => setLevelFilter(event.target.value)} aria-label={t('level')} className="dashboard-filter-select dashboard-filter-select-sm">
          <option value="all">{t('level')}</option><option value="CRITICAL">CRITICAL</option><option value="WARNING">WARNING</option>
        </Select>
        <Select value={applicationFilter} onChange={(event) => setApplicationFilter(event.target.value)} aria-label={tn('applications')} className="dashboard-filter-select">
          <option value="all">{tn('applications')}</option>{applications.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </Select>
        {hasActiveFilters && <Button variant="ghost" className="h-9 px-2" onClick={clearFilters}>{tc('clearFilters')}</Button>}
      </div>
      {loading && (
        <div className="analysis-record-list analysis-record-loading" aria-busy="true">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="analysis-record">
                <Skeleton className="h-4 w-44" />
                <Skeleton className="h-3.5 w-24" /><Skeleton className="h-3.5 w-28" />
                <Skeleton className="h-3.5 w-32" /><Skeleton className="h-3.5 w-20" />
              </div>
            ))}
        </div>
      )}
      {error && <div className="dashboard-error"><p className="muted" style={{ color: 'var(--danger)' }}>{error}</p><Button variant="outline" size="sm" onClick={() => setReloadKey((key) => key + 1)}>{tc('retry')}</Button></div>}
      {!loading && !error && analyses.length === 0 && (
        <p className="muted">{tc('empty')}</p>
      )}
      {!loading && !error && analyses.length > 0 && filteredAnalyses.length === 0 && <p className="muted dashboard-empty">{tc('empty')}</p>}
      {!loading && !error && filteredAnalyses.length > 0 && <div className="analysis-record-list" role="list" aria-label={t('title')}>
        {filteredAnalyses.map((analysis) => <div key={analysis.id} role="listitem">
          <Link className="analysis-record" href={`/workbench/analysis/${analysis.id}`} aria-label={`${t('view')} ${analysis.title}`}>
            <span className="analysis-record-title">{analysis.title || analysis.dedupeKey}</span>
            <span className={`table-status table-status-${statusVariant(analysis.status)}`}><i />{analysis.status}</span>
            <span className={`analysis-environment analysis-environment-${analysis.level === 'CRITICAL' ? 'critical' : 'warning'}`}>{analysis.level}</span>
            <span className="analysis-record-application">{analysis.applicationName}</span>
            <span className="analysis-record-key mono" title={analysis.dedupeKey}>{analysis.dedupeKey}</span>
            <IconArrowUpRight className="analysis-record-arrow" size={15} aria-hidden="true" />
          </Link>
        </div>)}
      </div>}
    </div>
  );
}
