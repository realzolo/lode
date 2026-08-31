'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Check, FileSearch, GitBranch, GitMerge, Pause, Play, Plus, RefreshCw, RotateCcw, Split, Square } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { InvestigationExecutionFlow } from '@/components/investigation-execution-flow';
import { ActionProposalSection, CreateActionDialog, IncidentActionSection } from '@/components/incident-actions';
import { IncidentReportPanel } from '@/components/incident-report-panel';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  addInvestigationEvidence, apiErrorMessage, askInvestigationQuestion, assignIncident,
  branchInvestigation, classifyIncidentSeverity, compareInvestigations, controlInvestigation,
  createInvestigationReview, decideActionProposal, fetchActionProposals,
  fetchIncident, fetchIncidentAssignees, fetchInvestigationExecutionGraph,
  fetchInvestigationReport, fetchInvestigationReviews, fetchRepositories,
  fetchSimilarIncidents, mergeIncident, retryIncidentInvestigation, splitIncident,
  startIncidentInvestigation, transitionIncident, updateIncidentAction,
} from '@/lib/api';
import { Link } from '@/lib/navigation';
import type {
  ActionProposal, IncidentActionCapability, IncidentOverview,
  IncidentSeverity, IncidentState, InvestigationExecutionGraph, InvestigationReportView,
  InvestigationReview, InvestigationRun, RepositoryBinding, SimilarIncident, WorkspaceMember,
} from '@/lib/types';

type TransitionAction = 'acknowledge' | 'mitigate' | 'resolve' | 'close' | 'reopen';
type InterventionMode = 'evidence' | 'question' | 'branch';

function stateLabel(state: IncidentState, t: ReturnType<typeof useTranslations>) {
  return t({ open: 'stateOpen', acknowledged: 'stateAcknowledged', mitigated: 'stateMitigated', resolved: 'stateResolved', closed: 'stateClosed' }[state]);
}

function severityLabel(severity: IncidentSeverity, t: ReturnType<typeof useTranslations>) {
  return t(severity === 'CRITICAL' ? 'severityCritical' : severity === 'WARNING' ? 'severityWarning' : 'severityUnclassified');
}

function runStatusLabel(status: InvestigationRun['status'], t: ReturnType<typeof useTranslations>) {
  const keys: Record<InvestigationRun['status'], string> = {
    queued: 'statusQueued', running: 'statusRunning', paused: 'statusPaused', reporting: 'statusReporting',
    completed: 'statusCompleted', failed: 'statusFailed', cancelled: 'statusCancelled',
  };
  return t(keys[status]);
}

function capabilityMap(values: IncidentActionCapability[]) {
  return new Map(values.map((value) => [value.action, value]));
}

function maskedErrorText(value: Record<string, unknown>): string {
  const text = value.text ?? value.message ?? value.stack;
  return typeof text === 'string' && text ? text : JSON.stringify(value);
}

export default function IncidentPage({ params }: { params: { incidentId: string } }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const locale = useLocale();
  const [incident, setIncident] = useState<IncidentOverview | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [repositories, setRepositories] = useState<RepositoryBinding[]>([]);
  const [proposals, setProposals] = useState<ActionProposal[]>([]);
  const [similar, setSimilar] = useState<SimilarIncident[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [report, setReport] = useState<InvestigationReportView | null>(null);
  const [reviews, setReviews] = useState<InvestigationReview[]>([]);
  const [graph, setGraph] = useState<InvestigationExecutionGraph | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [assigneeId, setAssigneeId] = useState('unassigned');
  const [reason, setReason] = useState('');
  const [selectedSignals, setSelectedSignals] = useState<number[]>([]);
  const [mergeSourceId, setMergeSourceId] = useState('');
  const [splitTitle, setSplitTitle] = useState('');
  const [interventionMode, setInterventionMode] = useState<InterventionMode>('evidence');
  const [interventionTitle, setInterventionTitle] = useState('');
  const [interventionText, setInterventionText] = useState('');
  const [comparisonRunId, setComparisonRunId] = useState('');
  const [comparison, setComparison] = useState<Record<string, unknown> | null>(null);
  const [reviewVerdict, setReviewVerdict] = useState<'accepted' | 'rejected' | 'needs_evidence'>('accepted');
  const [reviewComment, setReviewComment] = useState('');
  const [supersedesReviewId, setSupersedesReviewId] = useState('');
  const [actionDialogOpen, setActionDialogOpen] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const dateLocale = locale === 'zh' ? 'zh-CN' : 'en-US';

  const load = useCallback(async () => {
    try {
      const value = await fetchIncident(params.incidentId);
      const [nextMembers, nextRepositories, nextProposals, nextSimilar] = await Promise.all([
        fetchIncidentAssignees(value.id).catch(() => []),
        fetchRepositories(value.workspace_id).catch(() => []),
        fetchActionProposals(value.id).catch(() => []),
        fetchSimilarIncidents(value.id).catch(() => []),
      ]);
      setIncident(value);
      setMembers(nextMembers);
      setRepositories(nextRepositories);
      setProposals(nextProposals);
      setSimilar(nextSimilar);
      setSelectedRunId((current) => current && value.investigations.some((run) => run.id === current) ? current : value.investigations[0]?.id ?? null);
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setLoading(false);
    }
  }, [params.incidentId, tc]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setAssigneeId(incident?.assigned_to ? String(incident.assigned_to) : 'unassigned'); }, [incident?.assigned_to]);
  useEffect(() => {
    if (selectedRunId === null) { setReport(null); setReviews([]); setGraph(null); return; }
    let active = true;
    void Promise.all([
      fetchInvestigationReport(selectedRunId).catch(() => null),
      fetchInvestigationReviews(selectedRunId).catch(() => []),
      fetchInvestigationExecutionGraph(selectedRunId).catch(() => null),
    ]).then(([nextReport, nextReviews, nextGraph]) => {
      if (active) { setReport(nextReport); setReviews(nextReviews); setGraph(nextGraph); }
    });
    return () => { active = false; };
  }, [selectedRunId]);

  const capabilities = useMemo(() => capabilityMap(incident?.allowed_actions ?? []), [incident?.allowed_actions]);
  const selectedRun = incident?.investigations.find((run) => run.id === selectedRunId) ?? null;
  const repositoryNames = useMemo(() => new Map(repositories.map((row) => [row.id, row.full_name])), [repositories]);

  async function mutate(operation: () => Promise<unknown>) {
    setMutating(true);
    try { await operation(); await load(); setError(''); }
    catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setMutating(false); }
  }

  async function runTransition(action: TransitionAction) {
    if (!incident || !reason.trim()) return;
    await mutate(() => transitionIncident(incident.id, action, { expected_state_version: incident.state_version, reason: reason.trim() }));
    setReason('');
  }

  async function classify(severity: 'WARNING' | 'CRITICAL') {
    if (!incident || !reason.trim()) return;
    await mutate(() => classifyIncidentSeverity(incident.id, { severity, expected_state_version: incident.state_version, reason: reason.trim() }));
    setReason('');
  }

  async function startRun() {
    if (!incident) return;
    await mutate(async () => { const created = await startIncidentInvestigation(incident.id); setSelectedRunId(created.id); });
  }

  async function retryRun(run: InvestigationRun) {
    if (!incident) return;
    await mutate(async () => { const created = await retryIncidentInvestigation(incident.id, run.id); setSelectedRunId(created.id); });
  }

  async function assign() {
    if (!incident || !reason.trim()) return;
    await mutate(() => assignIncident(incident.id, { owner_id: assigneeId === 'unassigned' ? null : Number(assigneeId), expected_state_version: incident.state_version, reason: reason.trim() }));
    setReason('');
  }

  async function splitSignals() {
    if (!incident || !selectedSignals.length || !splitTitle.trim() || !reason.trim()) return;
    await mutate(() => splitIncident(incident.id, selectedSignals, splitTitle.trim(), reason.trim()));
    setSelectedSignals([]); setSplitTitle(''); setReason('');
  }

  async function merge() {
    if (!incident || !mergeSourceId || !reason.trim()) return;
    await mutate(() => mergeIncident(incident.id, Number(mergeSourceId), reason.trim()));
    setMergeSourceId(''); setReason('');
  }

  async function submitIntervention() {
    if (!selectedRun || !interventionText.trim()) return;
    await mutate(() => interventionMode === 'evidence'
      ? addInvestigationEvidence(selectedRun.id, interventionTitle.trim() || t('additionalEvidence'), interventionText.trim())
      : interventionMode === 'question'
        ? askInvestigationQuestion(selectedRun.id, interventionText.trim())
        : branchInvestigation(selectedRun.id, interventionText.trim()));
    setInterventionTitle(''); setInterventionText('');
  }

  async function compare() {
    if (!selectedRun || !comparisonRunId) return;
    setMutating(true);
    try { setComparison(await compareInvestigations(Number(comparisonRunId), selectedRun.id)); }
    catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); }
    finally { setMutating(false); }
  }

  async function review() {
    if (!selectedRun || !reviewComment.trim()) return;
    await mutate(() => createInvestigationReview(selectedRun.id, { verdict: reviewVerdict, comment: reviewComment.trim(), ...(supersedesReviewId ? { supersedes_review_id: Number(supersedesReviewId) } : {}) }));
    setReviewComment(''); setSupersedesReviewId('');
    setReviews(await fetchInvestigationReviews(selectedRun.id).catch(() => []));
  }

  function locateEvidence(evidenceId: number) {
    const node = graph?.nodes.find((item) => item.evidence_refs.includes(evidenceId));
    if (!node) return;
    setSelectedNodeId(node.id);
    window.setTimeout(() => document.getElementById('investigation-process')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0);
  }

  if (loading || !incident) return <main className="p-8 text-sm text-muted-foreground">{error || tc('loading')}</main>;
  const transitionActions: TransitionAction[] = ['acknowledge', 'mitigate', 'resolve', 'close', 'reopen'];

  return <main className="dashboard-page space-y-7">
    <header className="flex flex-wrap items-start justify-between gap-4 border-b pb-5">
      <div className="space-y-2"><Button size="sm" variant="ghost" asChild><Link href="/workbench"><ArrowLeft size={15} />{t('title')}</Link></Button><p className="eyebrow">{t('incident')}</p><h1 className="page-title">{incident.title}</h1><p className="mono text-xs text-muted-foreground">#{incident.id}</p></div>
      <div className="flex flex-wrap gap-2">{capabilities.get('start_investigation')?.allowed && <Button variant="primary" loading={mutating} onClick={() => void startRun()}><Play size={16} />{t('startInvestigation')}</Button>}{capabilities.get('create_action')?.allowed && <Button variant="outline" onClick={() => setActionDialogOpen(true)}><Plus size={16} />{t('createAction')}</Button>}<Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button></div>
    </header>

    <section className="grid gap-4 border-b pb-6 sm:grid-cols-2 lg:grid-cols-5">
      <Stat label={t('state')} value={stateLabel(incident.state, t)} /><Stat label={t('severity')} value={severityLabel(incident.severity, t)} /><Stat label={t('signals')} value={String(incident.signal_count)} /><Stat label={t('lastObserved')} value={new Date(incident.last_occurred_at).toLocaleString(dateLocale)} /><Stat label={t('assignedTo')} value={members.find((member) => member.user_id === incident.assigned_to)?.display_name || t('unassigned')} />
    </section>

    <section className="space-y-3 border-b pb-6">
      <Input placeholder={t('stateReason')} value={reason} onChange={(event) => setReason(event.target.value)} />
      <div className="flex flex-wrap gap-2">{transitionActions.map((action) => capabilities.get(action)?.allowed && <Button key={action} size="sm" variant="outline" disabled={!reason.trim()} loading={mutating} onClick={() => void runTransition(action)}>{t(action === 'close' ? 'closeIncident' : action)}</Button>)}{incident.severity === 'UNCLASSIFIED' && <><Button size="sm" disabled={!reason.trim()} onClick={() => void classify('WARNING')}>{t('classifyWarning')}</Button><Button size="sm" variant="destructive" disabled={!reason.trim()} onClick={() => void classify('CRITICAL')}>{t('classifyCritical')}</Button></>}<Select className="w-48" value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)} aria-label={t('assignedTo')}><option value="unassigned">{t('unassigned')}</option>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</Select><Button size="sm" variant="outline" disabled={!reason.trim()} onClick={() => void assign()}>{t('assign')}</Button></div>
    </section>

    {error && <p className="border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}

    <section className="space-y-3">
      <h2 className="text-base font-semibold">{t('signals')}</h2>
      {incident.signals.length === 0 ? <p className="text-sm text-muted-foreground">{t('noSignals')}</p> : <div className="table-wrap"><table className="table"><thead><tr><th /><th>{t('observed')}</th><th>{t('source')}</th><th>{t('summary')}</th><th>{t('trace')}</th><th>{t('errorRepository')}</th></tr></thead><tbody>{incident.signals.map((signal) => <tr key={signal.id}><td><input type="checkbox" aria-label={t('selectSignal', { id: signal.id })} checked={selectedSignals.includes(signal.id)} onChange={() => setSelectedSignals((current) => current.includes(signal.id) ? current.filter((id) => id !== signal.id) : [...current, signal.id])} /></td><td className="whitespace-nowrap text-xs">{new Date(signal.observed_at).toLocaleString(dateLocale)}</td><td>{signal.source_type === 'kafka' ? 'Kafka' : t('sourceManual')}</td><td><strong>{signal.title}</strong><p className="mt-1 max-w-xl whitespace-pre-wrap text-xs text-muted-foreground">{signal.summary}</p><p className="mt-1 max-w-xl whitespace-pre-wrap font-mono text-xs">{maskedErrorText(signal.error_masked)}</p></td><td>{signal.has_trace ? t('present') : t('missing')}</td><td>{signal.repository_binding_id ? repositoryNames.get(signal.repository_binding_id) || `#${signal.repository_binding_id}` : <span className="text-warning">{t('sourceUnknown')}</span>}</td></tr>)}</tbody></table></div>}
      <div className="flex flex-wrap gap-2 border-t pt-3"><Input className="min-w-56 flex-1" placeholder={t('splitTitle')} value={splitTitle} onChange={(event) => setSplitTitle(event.target.value)} /><Button variant="outline" disabled={!selectedSignals.length || !splitTitle.trim() || !reason.trim()} onClick={() => void splitSignals()}><Split size={15} />{t('splitIncident')}</Button><Input className="w-48" inputMode="numeric" placeholder={t('mergeSourceId')} value={mergeSourceId} onChange={(event) => setMergeSourceId(event.target.value)} /><Button variant="outline" disabled={!mergeSourceId || !reason.trim()} onClick={() => void merge()}><GitMerge size={15} />{t('mergeIncident')}</Button></div>
    </section>

    <section className="space-y-3 border-t pt-6"><h2 className="text-base font-semibold">{t('investigations')}</h2>{incident.investigations.length === 0 ? <p className="text-sm text-muted-foreground">{t('noInvestigations')}</p> : <div className="divide-y border-y">{incident.investigations.map((run) => <div key={run.id} className={`flex flex-wrap items-center justify-between gap-3 py-3 ${run.id === selectedRunId ? 'text-primary' : ''}`}><button type="button" className="text-left" onClick={() => setSelectedRunId(run.id)}><strong>{t('run')} #{run.id}</strong><p className="mt-1 text-sm text-muted-foreground">{runStatusLabel(run.status, t)} · {t(`triggerReasons.${run.trigger_reason}`)}</p></button><div className="flex gap-2">{(['completed', 'failed'].includes(run.status)) && <Button size="sm" variant="outline" onClick={() => void retryRun(run)}><RotateCcw size={15} />{t('retryInvestigation')}</Button>}<Button size="icon" variant="ghost" aria-label={t('viewReport')} onClick={() => setSelectedRunId(run.id)}><FileSearch size={15} /></Button></div></div>)}</div>}</section>

    {selectedRun && <>
      <section className="space-y-4 border-t pt-6"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="eyebrow">{t('investigationControl')}</p><h2 className="text-base font-semibold">{t('run')} #{selectedRun.id}</h2></div><div className="flex gap-2">{['queued', 'running', 'reporting'].includes(selectedRun.status) && <><Button size="sm" variant="outline" onClick={() => void mutate(() => controlInvestigation(selectedRun.id, 'pause', reason || t('operatorPause')))}><Pause size={15} />{t('pause')}</Button><Button size="sm" variant="destructive" onClick={() => void mutate(() => controlInvestigation(selectedRun.id, 'cancel', reason || t('operatorCancel')))}><Square size={15} />{t('cancelRun')}</Button></>}{selectedRun.status === 'paused' && <Button size="sm" onClick={() => void mutate(() => controlInvestigation(selectedRun.id, 'resume', reason || t('operatorResume')))}><Play size={15} />{t('resume')}</Button>}</div></div>
        <div className="grid gap-3 lg:grid-cols-[180px_1fr_auto]"><Select value={interventionMode} onChange={(event) => setInterventionMode(event.target.value as InterventionMode)}><option value="evidence">{t('addEvidence')}</option><option value="question">{t('followUpQuestion')}</option><option value="branch">{t('hypothesisBranch')}</option></Select>{interventionMode === 'evidence' ? <div className="grid gap-2"><Input placeholder={t('evidenceDescription')} value={interventionTitle} onChange={(event) => setInterventionTitle(event.target.value)} /><Textarea placeholder={t('evidenceText')} value={interventionText} onChange={(event) => setInterventionText(event.target.value)} /></div> : <Textarea placeholder={interventionMode === 'question' ? t('question') : t('hypothesis')} value={interventionText} onChange={(event) => setInterventionText(event.target.value)} />}<Button disabled={!interventionText.trim()} onClick={() => void submitIntervention()}><GitBranch size={15} />{tc('save')}</Button></div>
        {incident.investigations.length > 1 && <div className="grid gap-2 border-t pt-3 sm:grid-cols-[240px_auto_1fr]"><Select value={comparisonRunId} onChange={(event) => setComparisonRunId(event.target.value)}><option value="">{t('compareWith')}</option>{incident.investigations.filter((run) => run.id !== selectedRun.id).map((run) => <option key={run.id} value={run.id}>{t('run')} #{run.id}</option>)}</Select><Button variant="outline" disabled={!comparisonRunId} onClick={() => void compare()}>{t('compareRuns')}</Button>{comparison && <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{JSON.stringify(comparison, null, 2)}</pre>}</div>}
      </section>

      <section id="investigation-process" className="space-y-4 scroll-mt-6 border-t pt-6"><div><p className="eyebrow">{t('latestReport')}</p><h2 className="text-base font-semibold">{t('run')} #{selectedRun.id}</h2></div>{report ? <IncidentReportPanel report={report} onEvidence={locateEvidence} /> : <p className="text-sm text-muted-foreground">{t('noReport')}</p>}<InvestigationExecutionFlow investigationId={selectedRun.id} graph={graph} selectedNodeId={selectedNodeId} onSelectedNodeIdChange={setSelectedNodeId} focusRequest={null} /></section>

      <section className="space-y-3 border-t pt-6"><h2 className="text-base font-semibold">{t('reviews')}</h2><div className="grid gap-2 lg:grid-cols-[180px_220px_1fr_auto]"><Select value={reviewVerdict} onChange={(event) => setReviewVerdict(event.target.value as typeof reviewVerdict)}><option value="accepted">{t('reviewAccepted')}</option><option value="rejected">{t('reviewRejected')}</option><option value="needs_evidence">{t('reviewNeedsEvidence')}</option></Select><Select value={supersedesReviewId} onChange={(event) => setSupersedesReviewId(event.target.value)}><option value="">{t('newIndependentReview')}</option>{reviews.filter((item) => !reviews.some((next) => next.supersedes_review_id === item.id)).map((item) => <option key={item.id} value={item.id}>{t('correctReview', { id: item.id })}</option>)}</Select><Input placeholder={t('reviewComment')} value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} /><Button disabled={!reviewComment.trim()} onClick={() => void review()}><Check size={15} />{t('recordReview')}</Button></div>{reviews.length === 0 ? <p className="text-sm text-muted-foreground">{t('noReviews')}</p> : <div className="divide-y border-y">{reviews.map((item) => <div key={item.id} className="py-3 text-sm"><div className="flex justify-between"><strong>{t(`reviewVerdicts.${item.verdict}`)}</strong><span className="text-muted-foreground">{new Date(item.created_at).toLocaleString(dateLocale)}</span></div><p className="mt-1">{item.comment}</p>{item.supersedes_review_id && <p className="mt-1 text-xs text-muted-foreground">{t('supersedesReview', { id: item.supersedes_review_id })}</p>}</div>)}</div>}</section>
    </>}

    <ActionProposalSection proposals={proposals} members={members} onDecide={(proposal, decision, owner, decisionReason) => mutate(() => decideActionProposal(incident.id, proposal.id, decision, { reason: decisionReason, ...(owner ? { owner_id: owner } : {}) }))} />
    <IncidentActionSection actions={incident.actions} members={members} onUpdate={(action, status, owner) => mutate(() => updateIncidentAction(incident.id, action.id, { status, owner_id: owner, expected_state_version: action.state_version }))} />
    <section className="space-y-3 border-t pt-6"><h2 className="text-base font-semibold">{t('similarIncidents')}</h2>{similar.length === 0 ? <p className="text-sm text-muted-foreground">{t('noSimilarIncidents')}</p> : <div className="divide-y border-y">{similar.map((item) => <div key={item.investigation_id} className="py-3"><div className="flex flex-wrap justify-between gap-2"><Link className="font-medium underline-offset-4 hover:underline" href={`/workbench/incident/${item.incident_id}`}>{item.headline}</Link><span className="text-xs text-muted-foreground">{Math.round(item.similarity * 100)}% · {t('clueOnly')}</span></div><p className="mt-1 text-sm text-muted-foreground">{item.executive_summary}</p></div>)}</div>}</section>
    <section className="space-y-3 border-t pt-6"><h2 className="text-base font-semibold">{t('incidentTimeline')}</h2><div className="divide-y border-y">{incident.timeline.map((event) => <div key={event.id} className="flex flex-wrap justify-between gap-2 py-3 text-sm"><span>{t(`eventTypes.${event.event_type}`)}</span><span className="text-muted-foreground">{new Date(event.created_at).toLocaleString(dateLocale)}</span></div>)}</div></section>
    <CreateActionDialog open={actionDialogOpen} onOpenChange={setActionDialogOpen} incident={incident} members={members} onCreated={load} />
  </main>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-medium">{value}</p></div>;
}
