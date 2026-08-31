'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, Check, ChevronDown, Plus, RefreshCw, Search, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { apiErrorMessage, createManualIncident, decideCorrelationCandidate, fetchCorrelationCandidates, fetchIncidents, fetchRepositories, fetchWorkspaceMembers, fetchWorkspaces } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';
import type { CorrelationCandidate, IncidentSeverity, IncidentState, IncidentSummary, RepositoryBinding, Workspace, WorkspaceMember } from '@/lib/types';

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
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setCandidateMutating(false);
    }
  }

  const names = useMemo(() => new Map(workspaces.map((row) => [row.id, row.name])), [workspaces]);
  const dateLocale = locale === 'zh' ? 'zh-CN' : 'en-US';

  return <main className="dashboard-page space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="eyebrow">{t('eyebrow')}</p>
        <h1 className="page-title">{t('title')}</h1>
        <p className="page-subtitle">{t('subtitle')}</p>
      </div>
      <div className="flex gap-2">
        <Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
        <Button variant="primary" onClick={() => setOpen(true)}><Plus size={16} />{t('new')}</Button>
      </div>
    </header>
    <div className="flex flex-wrap gap-2 border-y py-3">
      <label className="relative min-w-64 flex-1">
        <Search className="absolute left-3 top-2.5 text-muted-foreground" size={16} />
        <Input className="pl-9" placeholder={t('search')} value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      <Select className="w-44" value={state} onChange={(event) => setState(event.target.value)} aria-label={t('state')}>
        <option value="all">{t('allStates')}</option>
        {(['open', 'acknowledged', 'mitigated', 'resolved', 'closed'] as IncidentState[]).map((value) => <option key={value} value={value}>{stateLabel(value, t)}</option>)}
      </Select>
      <Select className="w-44" value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} aria-label={t('workspace')}>
        <option value="all">{t('allWorkspaces')}</option>
        {workspaces.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
      </Select>
      <Select className="w-44" value={severity} onChange={(event) => setSeverity(event.target.value)} aria-label={t('severity')}>
        <option value="all">{t('allSeverities')}</option>
        {(['UNCLASSIFIED', 'WARNING', 'CRITICAL'] as IncidentSeverity[]).map((value) => <option key={value} value={value}>{severityLabel(value, t)}</option>)}
      </Select>
      <Select className="w-40" value={sourceType} onChange={(event) => setSourceType(event.target.value)} aria-label={t('source')}>
        <option value="all">{t('allSources')}</option><option value="kafka">{t('sourceKafka')}</option><option value="manual">{t('sourceManual')}</option>
      </Select>
      <Select className="w-44" value={reportState} onChange={(event) => setReportState(event.target.value)} aria-label={t('reportState')}><option value="all">{t('allReportStates')}</option>{(['confirmed', 'hypothesis', 'insufficient', 'unavailable'] as const).map((value) => <option key={value} value={value}>{t(`reportStates.${value}`)}</option>)}</Select>
      <Select className="w-44" value={assignedTo} disabled={workspaceId === 'all'} onChange={(event) => setAssignedTo(event.target.value)} aria-label={t('assignedTo')}><option value="all">{t('allOwners')}</option>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</Select>
      <label className="field"><span className="sr-only">{t('observedFrom')}</span><Input type="datetime-local" value={observedFrom} onChange={(event) => setObservedFrom(event.target.value)} aria-label={t('observedFrom')} /></label>
      <label className="field"><span className="sr-only">{t('observedTo')}</span><Input type="datetime-local" value={observedTo} onChange={(event) => setObservedTo(event.target.value)} aria-label={t('observedTo')} /></label>
      {(query || state !== 'all' || workspaceId !== 'all' || severity !== 'all' || sourceType !== 'all' || reportState !== 'all' || assignedTo !== 'all' || observedFrom || observedTo) && <Button size="icon" variant="ghost" aria-label={tc('clearFilters')} title={tc('clearFilters')} onClick={() => { setQuery(''); setState('all'); setWorkspaceId('all'); setSeverity('all'); setSourceType('all'); setReportState('all'); setAssignedTo('all'); setObservedFrom(''); setObservedTo(''); }}><X size={16} /></Button>}
    </div>
    {error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    {candidates.length > 0 && <section className="space-y-3 border-y py-4"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="eyebrow">{t('correlationReview')}</p><h2 className="text-base font-semibold">{t('correlationCandidates')}</h2></div><Input className="max-w-md" placeholder={t('correlationDecisionReason')} value={candidateReason} onChange={(event) => setCandidateReason(event.target.value)} /></div><div className="divide-y border-y">{candidates.map((candidate) => <article key={candidate.id} className="flex flex-wrap items-center justify-between gap-3 py-3"><div><p className="text-sm"><Link className="font-medium hover:underline" href={`/workbench/incident/${candidate.current_incident_id}`}>#{candidate.current_incident_id}</Link> → <Link className="font-medium hover:underline" href={`/workbench/incident/${candidate.candidate_incident_id}`}>#{candidate.candidate_incident_id}</Link></p><p className="mt-1 text-xs text-muted-foreground">{t('correlationScore', { score: Math.round(candidate.score * 100) })} · {Object.entries(candidate.factors).filter(([, value]) => value === true).map(([key]) => key).join(', ') || t('noStrongFactor')}</p></div><div className="flex gap-2"><Button size="sm" disabled={!candidateReason.trim()} loading={candidateMutating} onClick={() => void decideCandidate(candidate.id, 'accept')}><Check size={14} />{t('accept')}</Button><Button size="sm" variant="outline" disabled={!candidateReason.trim()} onClick={() => void decideCandidate(candidate.id, 'reject')}><X size={14} />{t('reject')}</Button></div></article>)}</div></section>}
    {loading ? <ListSkeleton rows={6} columns={6} /> : <div className="operational-table">
      <div className="table-wrap"><table className="table"><thead><tr><th>{t('incident')}</th><th>{t('workspace')}</th><th>{t('state')}</th><th>{t('signals')}</th><th>{t('lastObserved')}</th><th /></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}>
          <td><p className="font-medium">{row.title}</p><p className="mono mt-1 text-xs text-muted-foreground">#{row.id}</p></td>
          <td>{names.get(row.workspace_id) || row.workspace_id}</td>
          <td><span className={`table-status table-status-${row.state === 'closed' ? 'neutral' : row.state === 'resolved' ? 'success' : row.state === 'mitigated' ? 'warning' : 'danger'}`}><i />{stateLabel(row.state, t)}</span><p className="mt-1 text-xs text-muted-foreground">{severityLabel(row.severity, t)}</p></td>
          <td>{row.signal_count}</td>
          <td className="text-xs text-muted-foreground">{new Date(row.last_occurred_at).toLocaleString(dateLocale)}</td>
          <td><Button size="icon" variant="ghost" asChild><Link href={`/workbench/incident/${row.id}`} aria-label={tc('open')} title={tc('open')}><ArrowUpRight size={16} /></Link></Button></td>
        </tr>)}</tbody>
      </table></div>
      {rows.length === 0 && <p className="p-8 text-center text-muted-foreground">{t('noMatching')}</p>}
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
      <Select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}><option value="">{t('workspacePlaceholder')}</option>{workspaces.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</Select>
      <Input placeholder={t('manualSummary')} value={summary} maxLength={2000} onChange={(event) => setSummary(event.target.value)} />
      <Textarea className="mono min-h-48" placeholder={t('errorText')} value={errorText} maxLength={50000} onChange={(event) => setErrorText(event.target.value)} />
      <details className="border-t pt-3">
        <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium">{t('optionalDetails')}<ChevronDown size={16} /></summary>
        <div className="mt-4 grid gap-3">
          <Input placeholder={t('traceId')} value={trace} maxLength={500} onChange={(event) => setTrace(event.target.value)} />
          <Select value={repositoryBindingId} onChange={(event) => setRepositoryBindingId(event.target.value)}>
            <option value="">{t('errorRepositoryUnknown')}</option>
            {repositories.map((row) => <option key={row.id} value={row.id}>{row.full_name}</option>)}
          </Select>
        </div>
      </details>
    </div>
    {error && <p className="text-sm text-destructive">{error}</p>}
    <DialogFooter><Button variant="outline" disabled={creating} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={creating} loadingText={tc('loading')} disabled={!workspaceId || !summary.trim() || !errorText.trim()} onClick={() => void create()}>{tc('start')}</Button></DialogFooter>
  </DialogContent></Dialog>;
}
