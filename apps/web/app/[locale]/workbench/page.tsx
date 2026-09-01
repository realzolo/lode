'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, Check, ChevronDown, Plus, RefreshCw, Search, SlidersHorizontal, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { TableEmptyState } from '@/components/ui/empty-state';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { TableColumns } from '@/components/ui/table';
import { apiErrorMessage, createManualIncident, decideCorrelationCandidate, fetchCorrelationCandidates, fetchIncidents, fetchRepositories, fetchWorkspaceMembers, fetchWorkspaces } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';
import type { CorrelationCandidate, IncidentSeverity, IncidentState, IncidentSummary, RepositoryBinding, Workspace, WorkspaceMember } from '@/lib/types';
import { relativeTime } from '@/lib/utils';

function stateLabel(state: IncidentState, t: ReturnType<typeof useTranslations>) {
  return t({
    open: 'stateOpen',
    acknowledged: 'stateAcknowledged',
    mitigated: 'stateMitigated',
    resolved: 'stateResolved',
    closed: 'stateClosed',
  }[state]);
}

function severityLabel(severity: IncidentSummary['severity'], t: ReturnType<typeof useTranslations>) {
  return t(severity === 'CRITICAL' ? 'severityCritical' : severity === 'WARNING' ? 'severityWarning' : 'severityUnclassified');
}

function incidentStateTone(state: IncidentState) {
  return {
    open: 'danger',
    acknowledged: 'accent',
    mitigated: 'warning',
    resolved: 'success',
    closed: 'neutral',
  }[state];
}

export default function IncidentsPage() {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const locale = useLocale();
  const router = useRouter();
  const [rows, setRows] = useState<IncidentSummary[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [query, setQuery] = useState('');
  const [state, setState] = useState('all');
  const [workspaceId, setWorkspaceId] = useState('all');
  const [severity, setSeverity] = useState('all');
  const [sourceType, setSourceType] = useState('all');
  const [reportState, setReportState] = useState('all');
  const [assignedTo, setAssignedTo] = useState('all');
  const [observedFrom, setObservedFrom] = useState('');
  const [observedTo, setObservedTo] = useState('');
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [candidates, setCandidates] = useState<CorrelationCandidate[]>([]);
  const [candidateReason, setCandidateReason] = useState('');
  const [candidateMutating, setCandidateMutating] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (append = false, cursor?: string) => {
    if (!append) setRefreshing(true);
    try {
      const [page, scopes] = await Promise.all([
        fetchIncidents({
          workspaceId: workspaceId === 'all' ? undefined : Number(workspaceId),
          state,
          severity,
          sourceType,
          reportState,
          assignedTo: assignedTo === 'all' ? undefined : Number(assignedTo),
          observedFrom: observedFrom ? new Date(observedFrom).toISOString() : undefined,
          observedTo: observedTo ? new Date(observedTo).toISOString() : undefined,
          q: query,
          cursor,
        }),
        append ? Promise.resolve(null) : fetchWorkspaces(),
      ]);
      setRows((current) => append ? [...current, ...page.items] : page.items);
      if (scopes) setWorkspaces(scopes);
      setNextCursor(page.next_cursor);
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [assignedTo, observedFrom, observedTo, query, reportState, severity, sourceType, state, tc, workspaceId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    setAssignedTo('all');
    if (workspaceId === 'all') { setMembers([]); return; }
    let active = true;
    void fetchWorkspaceMembers(Number(workspaceId)).then((value) => { if (active) setMembers(value); }).catch(() => { if (active) setMembers([]); });
    return () => { active = false; };
  }, [workspaceId]);
  const loadCandidates = useCallback(async () => {
    const ids = workspaceId === 'all' ? workspaces.map((row) => row.id) : [Number(workspaceId)];
    if (!ids.length) { setCandidates([]); return; }
    const values = await Promise.all(ids.map((id) => fetchCorrelationCandidates(id).catch(() => [])));
    setCandidates(values.flat().sort((left, right) => right.score - left.score || right.id - left.id));
  }, [workspaceId, workspaces]);
  useEffect(() => { void loadCandidates(); }, [loadCandidates]);

  async function decideCandidate(candidateId: number, decision: 'accept' | 'reject') {
    if (!candidateReason.trim()) return;
    setCandidateMutating(true);
    try {
      await decideCorrelationCandidate(candidateId, decision, candidateReason.trim());
      setCandidateReason('');
      await Promise.all([load(), loadCandidates()]);
    } catch (cause) {
      toast.error(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setCandidateMutating(false);
    }
  }

  const names = useMemo(() => new Map(workspaces.map((row) => [row.id, row.name])), [workspaces]);
  const dateLocale = locale === 'zh' ? 'zh-CN' : 'en-US';
  const filtersActive = Boolean(query || state !== 'all' || workspaceId !== 'all' || severity !== 'all' || sourceType !== 'all' || reportState !== 'all' || assignedTo !== 'all' || observedFrom || observedTo);
  const advancedFilterCount = [severity !== 'all', sourceType !== 'all', reportState !== 'all', assignedTo !== 'all', Boolean(observedFrom), Boolean(observedTo)].filter(Boolean).length;
  function clearFilters() { setQuery(''); setState('all'); setWorkspaceId('all'); setSeverity('all'); setSourceType('all'); setReportState('all'); setAssignedTo('all'); setObservedFrom(''); setObservedTo(''); }

  return <main className="dashboard-page space-y-6">
    <header className="dashboard-page-header">
      <div>
        <h1 className="page-title">{t('title')}</h1>
        <p className="page-subtitle">{t('subtitle')}</p>
      </div>
      <div className="flex gap-2">
        <Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('new')}</Button>
      </div>
    </header>
    <div className="dashboard-filterbar flex-wrap">
      <div className="dashboard-search">
        <Search className="shrink-0" size={16} aria-hidden="true" />
        <Input className="min-w-0 flex-1" aria-label={t('search')} placeholder={t('search')} value={query} onChange={(event) => setQuery(event.target.value)} />
      </div>
      <Select className="dashboard-filter-select" value={state} onChange={(event) => setState(event.target.value)} aria-label={t('state')}>
        <option value="all">{t('allStates')}</option>
        {(['open', 'acknowledged', 'mitigated', 'resolved', 'closed'] as IncidentState[]).map((value) => <option key={value} value={value}>{stateLabel(value, t)}</option>)}
      </Select>
      <Select className="dashboard-filter-select" value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} aria-label={t('workspace')}>
        <option value="all">{t('allWorkspaces')}</option>
        {workspaces.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
      </Select>
      <Popover>
        <PopoverTrigger asChild>
          <Button className="dashboard-filter-button" variant="outline">
            <SlidersHorizontal size={15} />
            {t('filters')}
            {advancedFilterCount ? <span className="dashboard-filter-count" aria-hidden="true">{advancedFilterCount}</span> : null}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="dashboard-filter-popover" aria-label={t('filters')}>
          <div className="dashboard-filter-popover-grid">
            <div className="dashboard-filter-field"><span>{t('severity')}</span><Select value={severity} onChange={(event) => setSeverity(event.target.value)} aria-label={t('severity')}><option value="all">{t('allSeverities')}</option>{(['UNCLASSIFIED', 'WARNING', 'CRITICAL'] as IncidentSeverity[]).map((value) => <option key={value} value={value}>{severityLabel(value, t)}</option>)}</Select></div>
            <div className="dashboard-filter-field"><span>{t('source')}</span><Select value={sourceType} onChange={(event) => setSourceType(event.target.value)} aria-label={t('source')}><option value="all">{t('allSources')}</option><option value="kafka">{t('sourceKafka')}</option><option value="manual">{t('sourceManual')}</option></Select></div>
            <div className="dashboard-filter-field"><span>{t('reportState')}</span><Select value={reportState} onChange={(event) => setReportState(event.target.value)} aria-label={t('reportState')}><option value="all">{t('allReportStates')}</option>{(['confirmed', 'hypothesis', 'insufficient', 'unavailable'] as const).map((value) => <option key={value} value={value}>{t(`reportStates.${value}`)}</option>)}</Select></div>
            <div className="dashboard-filter-field"><span>{t('assignedTo')}</span><Select value={assignedTo} disabled={workspaceId === 'all'} onChange={(event) => setAssignedTo(event.target.value)} aria-label={t('assignedTo')}><option value="all">{t('allOwners')}</option>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</Select></div>
            <label className="dashboard-filter-field"><span>{t('observedFrom')}</span><Input type="datetime-local" value={observedFrom} onChange={(event) => setObservedFrom(event.target.value)} aria-label={t('observedFrom')} /></label>
            <label className="dashboard-filter-field"><span>{t('observedTo')}</span><Input type="datetime-local" value={observedTo} onChange={(event) => setObservedTo(event.target.value)} aria-label={t('observedTo')} /></label>
          </div>
        </PopoverContent>
      </Popover>
      {filtersActive && <Button size="icon" variant="ghost" aria-label={tc('clearFilters')} title={tc('clearFilters')} onClick={clearFilters}><X size={16} /></Button>}
    </div>
    {error && rows.length > 0 ? <p className="dashboard-feedback" role="alert">{error}</p> : null}
    {candidates.length > 0 && <section className="dashboard-section space-y-3"><div className="dashboard-section-heading"><div><p className="eyebrow">{t('correlationReview')}</p><h2 className="dashboard-section-title">{t('correlationCandidates')}</h2></div><label className="field w-full max-w-md"><span className="field-label">{t('correlationDecisionReason')}</span><Input value={candidateReason} onChange={(event) => setCandidateReason(event.target.value)} /></label></div><div className="dashboard-record-list">{candidates.map((candidate) => <article key={candidate.id} className="dashboard-record flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm"><Link className="font-medium hover:underline" href={`/workbench/incident/${candidate.current_incident_id}`}>#{candidate.current_incident_id}</Link> → <Link className="font-medium hover:underline" href={`/workbench/incident/${candidate.candidate_incident_id}`}>#{candidate.candidate_incident_id}</Link></p><p className="mt-1 text-xs text-muted-foreground">{t('correlationScore', { score: Math.round(candidate.score * 100) })} · {Object.entries(candidate.factors).filter(([, value]) => value === true).map(([key]) => key).join(', ') || t('noStrongFactor')}</p></div><div className="flex gap-2"><Button size="sm" disabled={!candidateReason.trim()} loading={candidateMutating} onClick={() => void decideCandidate(candidate.id, 'accept')}><Check size={14} />{t('accept')}</Button><Button size="sm" variant="outline" disabled={!candidateReason.trim()} onClick={() => void decideCandidate(candidate.id, 'reject')}><X size={14} />{t('reject')}</Button></div></article>)}</div></section>}
    {loading ? <ListSkeleton rows={6} columns={6} /> : error && rows.length === 0 ? <TableEmptyState title={tc('requestFailed')} action={<Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw size={15} />{tc('retry')}</Button>} /> : rows.length === 0 ? <TableEmptyState title={t('noMatching')} action={filtersActive ? <Button size="sm" variant="outline" onClick={clearFilters}>{tc('clearFilters')}</Button> : <Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={15} />{t('new')}</Button>} /> : <div className="operational-table">
      <div className="table-wrap"><table className="table"><TableColumns widths={[34, 22, 16, 10, 18]} trailingWidth={64} /><thead><tr><th>{t('incident')}</th><th>{t('workspace')}</th><th>{t('state')}</th><th>{t('signals')}</th><th>{t('lastObserved')}</th><th><span className="sr-only">{tc('actions')}</span></th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}>
          <td><p><Link href={`/workbench/incident/${row.id}`} className="table-record-link">{row.title}</Link></p><p className="mono mt-1 text-xs text-muted-foreground">#{row.id}</p></td>
          <td>{names.get(row.workspace_id) ?? tc('unknown')}</td>
          <td><span className={`table-status table-status-${incidentStateTone(row.state)}`}><i aria-hidden="true" />{stateLabel(row.state, t)}</span><p className="mt-1 text-xs text-muted-foreground">{severityLabel(row.severity, t)}</p></td>
          <td className="table-number">{row.signal_count}</td>
          <td className="table-time text-xs text-muted-foreground" title={new Date(row.last_occurred_at).toLocaleString(dateLocale)}>{relativeTime(row.last_occurred_at, locale)}</td>
          <td><Button size="icon" variant="ghost" asChild aria-label={tc('open')} title={tc('open')}><Link href={`/workbench/incident/${row.id}`}><ArrowUpRight size={16} /></Link></Button></td>
        </tr>)}</tbody>
      </table></div>
    </div>}
    {nextCursor !== null && <div className="flex justify-center"><Button variant="outline" onClick={() => void load(true, nextCursor)}>{t('loadMore')}</Button></div>}
    <CreateIncidentDialog open={open} onOpenChange={setOpen} workspaces={workspaces} onCreated={(id) => router.push(`/workbench/incident/${id}`)} />
  </main>;
}

function CreateIncidentDialog({ open, onOpenChange, workspaces, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaces: Workspace[]; onCreated: (id: number) => void }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const [workspaceId, setWorkspaceId] = useState('');
  const [summary, setSummary] = useState('');
  const [errorText, setErrorText] = useState('');
  const [trace, setTrace] = useState('');
  const [repositoryBindingId, setRepositoryBindingId] = useState('');
  const [repositories, setRepositories] = useState<RepositoryBinding[]>([]);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    setRepositoryBindingId('');
    if (!workspaceId) {
      setRepositories([]);
      return;
    }
    let active = true;
    void fetchRepositories(Number(workspaceId)).then((rows) => {
      if (active) setRepositories(rows.filter((row) => row.state === 'active' && row.analysis_mode === 'code'));
    }).catch(() => { if (active) setRepositories([]); });
    return () => { active = false; };
  }, [workspaceId]);

  async function create() {
    setCreating(true);
    try {
      const result = await createManualIncident(Number(workspaceId), {
        schema_version: 'manual-incident.v1',
        summary: summary.trim(),
        error_text: errorText.trim(),
        ...(trace.trim() ? { trace_id: trace.trim() } : {}),
        ...(repositoryBindingId ? { repository_binding_id: Number(repositoryBindingId) } : {}),
      });
      onOpenChange(false);
      onCreated(result.incident_id);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setCreating(false);
    }
  }

  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('newTitle')}</DialogTitle></DialogHeader>
    <div className="grid gap-4">
      <label className="field"><span className="field-label">{t('workspacePlaceholder')}</span><Select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}><option value="">{t('workspacePlaceholder')}</option>{workspaces.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</Select></label>
      <label className="field"><span className="field-label">{t('manualSummary')}</span><Input value={summary} maxLength={2000} onChange={(event) => setSummary(event.target.value)} /></label>
      <label className="field"><span className="field-label">{t('errorText')}</span><Textarea className="mono min-h-48" value={errorText} maxLength={50000} onChange={(event) => setErrorText(event.target.value)} /></label>
      <details className="border-t pt-3">
        <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium">{t('optionalDetails')}<ChevronDown size={16} /></summary>
        <div className="mt-4 grid gap-3">
          <label className="field"><span className="field-label">{t('traceId')}</span><Input value={trace} maxLength={500} onChange={(event) => setTrace(event.target.value)} /></label>
          <label className="field"><span className="field-label">{t('errorRepositoryUnknown')}</span><Select value={repositoryBindingId} onChange={(event) => setRepositoryBindingId(event.target.value)}>
            <option value="">{t('errorRepositoryUnknown')}</option>
            {repositories.map((row) => <option key={row.id} value={row.id}>{row.full_name}</option>)}
          </Select></label>
        </div>
      </details>
    </div>
    {error && <p className="dashboard-feedback" role="alert">{error}</p>}
    <DialogFooter><Button variant="outline" disabled={creating} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={creating} loadingText={tc('loading')} disabled={!workspaceId || !summary.trim() || !errorText.trim()} onClick={() => void create()}>{tc('start')}</Button></DialogFooter>
  </DialogContent></Dialog>;
}
