'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowLeft, CheckCircle2, FileSearch, Play, Plus, RefreshCw, RotateCcw } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { InvestigationExecutionFlow } from '@/components/investigation-execution-flow';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  apiErrorMessage,
  assignIncident,
  createIncidentAction,
  fetchIncident,
  fetchIncidentAssignees,
  fetchInvestigationExecutionGraph,
  fetchInvestigationReport,
  retryIncidentInvestigation,
  startIncidentInvestigation,
  transitionIncident,
} from '@/lib/api';
import { Link } from '@/lib/navigation';
import type { IncidentActionCapability, IncidentOverview, IncidentState, InvestigationExecutionGraph, InvestigationReportView, InvestigationRun, WorkspaceMember } from '@/lib/types';

type TransitionAction = 'acknowledge' | 'mitigate' | 'resolve' | 'close' | 'reopen';

function stateLabel(state: IncidentState, t: ReturnType<typeof useTranslations>) {
  return t({ open: 'stateOpen', acknowledged: 'stateAcknowledged', mitigated: 'stateMitigated', resolved: 'stateResolved', closed: 'stateClosed' }[state]);
}

function runStatusLabel(status: InvestigationRun['status'], t: ReturnType<typeof useTranslations>) {
  return t({ queued: 'statusQueued', running: 'statusRunning', reporting: 'statusReporting', completed: 'statusCompleted', failed: 'statusFailed' }[status]);
}

function actionLabel(action: TransitionAction, t: ReturnType<typeof useTranslations>) {
  return t({ acknowledge: 'acknowledge', mitigate: 'mitigate', resolve: 'resolve', close: 'closeIncident', reopen: 'reopen' }[action]);
}

function capabilityMap(values: IncidentActionCapability[]) {
  return new Map(values.map((value) => [value.action, value]));
}

function asText(record: Record<string, unknown>, key: string, fallback: string) {
  const value = record[key];
  return typeof value === 'string' && value ? value : fallback;
}

export default function IncidentPage({ params }: { params: { incidentId: string } }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const locale = useLocale();
  const [incident, setIncident] = useState<IncidentOverview | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [report, setReport] = useState<InvestigationReportView | null>(null);
  const [graph, setGraph] = useState<InvestigationExecutionGraph | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [assigneeId, setAssigneeId] = useState('unassigned');
  const [reason, setReason] = useState('');
  const [actionDialogOpen, setActionDialogOpen] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const dateLocale = locale === 'zh' ? 'zh-CN' : 'en-US';

  const load = useCallback(async () => {
    try {
      const value = await fetchIncident(params.incidentId);
      setIncident(value);
      setMembers(await fetchIncidentAssignees(value.id).catch(() => []));
      setSelectedRunId((current) => current && value.investigations.some((run) => run.id === current) ? current : value.investigations[0]?.id ?? null);
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setLoading(false);
    }
  }, [params.incidentId, tc]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setAssigneeId(incident?.assigned_to ? String(incident.assigned_to) : 'unassigned'); }, [incident?.assigned_to, incident?.id]);
  useEffect(() => {
    if (selectedRunId === null) {
      setReport(null);
      setGraph(null);
      return;
    }
    let active = true;
    void Promise.all([
      fetchInvestigationReport(selectedRunId).catch(() => null),
      fetchInvestigationExecutionGraph(selectedRunId).catch(() => null),
    ]).then(([nextReport, nextGraph]) => {
      if (active) {
        setReport(nextReport);
        setGraph(nextGraph);
      }
    });
    return () => { active = false; };
  }, [selectedRunId]);

  const capabilities = useMemo(() => capabilityMap(incident?.allowed_actions ?? []), [incident?.allowed_actions]);
  const selectedRun = incident?.investigations.find((run) => run.id === selectedRunId) ?? null;

  async function runTransition(action: TransitionAction) {
    if (!incident || !reason.trim()) return;
    setMutating(true);
    try {
      const updated = await transitionIncident(incident.id, action, { expected_state_version: incident.state_version, reason: reason.trim() });
      setIncident(updated);
      setReason('');
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setMutating(false);
    }
  }

  async function startRun() {
    if (!incident) return;
    setMutating(true);
    try {
      const created = await startIncidentInvestigation(incident.id);
      await load();
      setSelectedRunId(created.id);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setMutating(false);
    }
  }

  async function retryRun(run: InvestigationRun) {
    if (!incident) return;
    setMutating(true);
    try {
      const created = await retryIncidentInvestigation(incident.id, run.id);
      await load();
      setSelectedRunId(created.id);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setMutating(false);
    }
  }

  async function assign() {
    if (!incident || !reason.trim()) return;
    setMutating(true);
    try {
      const updated = await assignIncident(incident.id, {
        owner_id: assigneeId === 'unassigned' ? null : Number(assigneeId),
        expected_state_version: incident.state_version,
        reason: reason.trim(),
      });
      setIncident(updated);
      setReason('');
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setMutating(false);
    }
  }

  if (loading || !incident) return <main className="p-8 text-sm text-muted-foreground">{error || tc('loading')}</main>;

  const transitionActions: TransitionAction[] = ['acknowledge', 'mitigate', 'resolve', 'close', 'reopen'];
  const canChangeState = transitionActions.some((action) => capabilities.get(action)?.allowed);

  return <main className="dashboard-page space-y-6">
    <header className="flex flex-wrap items-start justify-between gap-4 border-b pb-5">
      <div className="space-y-2">
        <Button size="sm" variant="ghost" asChild><Link href="/workbench"><ArrowLeft size={15} />{t('title')}</Link></Button>
        <p className="eyebrow">{t('incident')}</p>
        <h1 className="page-title">{incident.event}</h1>
        <p className="mono text-sm text-muted-foreground">{incident.component} · {incident.environment} · {incident.dedup_key}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {capabilities.get('start_investigation')?.allowed && <Button variant="primary" loading={mutating} onClick={() => void startRun()}><Play size={16} />{t('startInvestigation')}</Button>}
        {capabilities.get('create_action')?.allowed && <Button variant="outline" onClick={() => setActionDialogOpen(true)}><Plus size={16} />{t('createAction')}</Button>}
        <Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
      </div>
    </header>

    <section className="grid gap-4 border-b pb-6 md:grid-cols-5">
      <div><p className="text-xs text-muted-foreground">{t('state')}</p><p className="mt-1 font-medium">{stateLabel(incident.state, t)}</p></div>
      <div><p className="text-xs text-muted-foreground">{t('severity')}</p><p className="mt-1 font-medium">{incident.severity === 'CRITICAL' ? t('severityCritical') : t('severityWarning')}</p></div>
      <div><p className="text-xs text-muted-foreground">{t('occurrences')}</p><p className="mt-1 font-medium">{incident.occurrence_count}</p></div>
      <div><p className="text-xs text-muted-foreground">{t('latestOccurrence')}</p><p className="mt-1 text-sm">{new Date(incident.last_occurred_at).toLocaleString(dateLocale)}</p></div>
      <div><p className="text-xs text-muted-foreground">{t('assignedTo')}</p><p className="mt-1 text-sm">{members.find((member) => member.user_id === incident.assigned_to)?.display_name || (incident.assigned_to ? incident.assigned_to : t('unassigned'))}</p></div>
    </section>

    {canChangeState && <section className="space-y-3 border-b pb-6">
      <div className="flex flex-wrap items-center gap-2"><Input className="min-w-64 flex-1" placeholder={t('stateReason')} value={reason} onChange={(event) => setReason(event.target.value)} />
        {transitionActions.map((action) => capabilities.get(action)?.allowed && <Button key={action} size="sm" variant={action === 'close' ? 'outline' : 'primary'} disabled={!reason.trim()} loading={mutating} onClick={() => void runTransition(action)}>{actionLabel(action, t)}</Button>)}
        {capabilities.get('assign')?.allowed && <><Select className="w-48" value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)} aria-label={t('assignedTo')}><option value="unassigned">{t('unassigned')}</option>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</Select><Button size="sm" variant="outline" disabled={!reason.trim()} loading={mutating} onClick={() => void assign()}>{t('assign')}</Button></>}
      </div>
    </section>}

    {error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}

    <section className="space-y-3">
      <div className="flex items-center justify-between"><h2 className="text-base font-semibold">{t('occurrences')}</h2></div>
      {incident.occurrences.length === 0 ? <p className="text-sm text-muted-foreground">{t('noOccurrences')}</p> : <div className="table-wrap"><table className="table"><thead><tr><th>{t('occurred')}</th><th>{t('event')}</th><th>{t('severity')}</th><th>{t('component')}</th><th>{t('environment')}</th></tr></thead><tbody>{incident.occurrences.map((occurrence) => <tr key={occurrence.id}><td>{new Date(occurrence.occurred_at).toLocaleString(dateLocale)}</td><td>{occurrence.event}</td><td>{occurrence.severity === 'CRITICAL' ? t('severityCritical') : t('severityWarning')}</td><td>{occurrence.component}</td><td>{occurrence.environment}</td></tr>)}</tbody></table></div>}
    </section>

    <section className="space-y-3 border-t pt-6">
      <h2 className="text-base font-semibold">{t('investigations')}</h2>
      {incident.investigations.length === 0 ? <p className="text-sm text-muted-foreground">{t('noInvestigations')}</p> : <div className="grid gap-2">{incident.investigations.map((run) => <div key={run.id} className={`flex flex-wrap items-center justify-between gap-3 border p-3 ${run.id === selectedRunId ? 'border-primary' : ''}`}><button type="button" className="text-left" onClick={() => setSelectedRunId(run.id)}><strong>{t('run')} #{run.id}</strong><p className="mt-1 text-sm text-muted-foreground">{runStatusLabel(run.status, t)} · {run.trigger_reason}</p></button><div className="flex gap-2">{(['completed', 'failed'].includes(run.status) && capabilities.get('start_investigation')?.allowed) && <Button size="sm" variant="outline" loading={mutating} onClick={() => void retryRun(run)}><RotateCcw size={15} />{t('retryInvestigation')}</Button>}<Button size="sm" variant="ghost" onClick={() => setSelectedRunId(run.id)}><FileSearch size={15} />{t('viewReport')}</Button></div></div>)}</div>}
    </section>

    {selectedRun && <section className="space-y-4 border-t pt-6">
      <div><p className="eyebrow">{t('latestReport')}</p><h2 className="text-base font-semibold">{t('run')} #{selectedRun.id}</h2></div>
      {report ? <ReportPanel report={report} /> : <p className="text-sm text-muted-foreground">{t('noReport')}</p>}
      <InvestigationExecutionFlow investigationId={selectedRun.id} graph={graph} selectedNodeId={selectedNodeId} onSelectedNodeIdChange={setSelectedNodeId} focusRequest={null} />
    </section>}

    <section className="space-y-3 border-t pt-6"><h2 className="text-base font-semibold">{t('followUpActions')}</h2>{incident.actions.length === 0 ? <p className="text-sm text-muted-foreground">{t('noActions')}</p> : <div className="grid gap-2">{incident.actions.map((action) => <article key={action.id} className="border p-3"><div className="flex flex-wrap justify-between gap-2"><strong>{action.title}</strong><span className="text-sm text-muted-foreground">{action.status} · {action.priority}</span></div><p className="mt-2 text-sm">{action.rationale}</p><p className="mt-2 text-sm text-muted-foreground">{action.validation}</p></article>)}</div>}</section>

    <section className="space-y-3 border-t pt-6"><h2 className="text-base font-semibold">{t('incidentTimeline')}</h2><div className="grid gap-2">{incident.timeline.map((event) => <div key={event.id} className="flex flex-wrap justify-between gap-2 border-l-2 border-muted px-3 py-2 text-sm"><span>{event.event_type}</span><span className="text-muted-foreground">{new Date(event.created_at).toLocaleString(dateLocale)}</span></div>)}</div></section>

    <CreateActionDialog open={actionDialogOpen} onOpenChange={setActionDialogOpen} incident={incident} onCreated={load} />
  </main>;
}

function ReportPanel({ report }: { report: InvestigationReportView }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const cause = report.incident_cause;
  const diagnosis = report.code_diagnosis;
  return <div className="space-y-5 border p-4">
    <div><h3 className="font-semibold">{report.headline}</h3><p className="mt-2 text-sm leading-6">{report.summary}</p></div>
    <div className="grid gap-4 lg:grid-cols-2"><section><h3 className="font-semibold">{t('incidentCause')}</h3><p className="mt-2 text-sm">{asText(cause, 'mechanism', tc('empty'))}</p>{Array.isArray(cause.causal_chain) && <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">{cause.causal_chain.filter((item): item is string => typeof item === 'string').map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol>}</section><section><h3 className="font-semibold">{t('codeDiagnosis')}</h3><p className="mt-2 text-sm">{asText(diagnosis, 'summary', tc('empty'))}</p></section></div>
    {report.confirmed_facts.length > 0 && <section><h3 className="font-semibold">{t('confirmedFacts')}</h3><ul className="mt-2 space-y-1 text-sm">{report.confirmed_facts.map((fact, index) => <li key={`${index}-${fact.text}`} className="flex gap-2"><CheckCircle2 className="mt-0.5 shrink-0 text-primary" size={15} />{fact.text}</li>)}</ul></section>}
    {report.counter_evidence.length > 0 && <section><h3 className="font-semibold">{t('counterEvidence')}</h3><ul className="mt-2 space-y-1 text-sm">{report.counter_evidence.map((fact, index) => <li key={`${index}-${fact.text}`}>{fact.text}</li>)}</ul></section>}
    {report.evidence_gaps.length > 0 && <section><h3 className="font-semibold">{t('evidenceGaps')}</h3><ul className="mt-2 space-y-1 text-sm">{report.evidence_gaps.map((item, index) => <li key={`${index}-${item}`} className="flex gap-2"><AlertTriangle className="mt-0.5 shrink-0 text-warning" size={15} />{item}</li>)}</ul></section>}
    {report.code_findings.length > 0 && <section><h3 className="font-semibold">{t('codeFindings')}</h3><div className="mt-2 grid gap-2">{report.code_findings.map((finding) => <article key={finding.id} className="border p-3"><strong>{finding.path || tc('empty')}{finding.symbol ? ` · ${finding.symbol}` : ''}</strong><p className="mt-2 text-sm">{finding.faulty_behavior}</p><p className="mt-1 text-sm text-muted-foreground">{finding.why_wrong}</p></article>)}</div></section>}
    <details className="border-t pt-3"><summary className="cursor-pointer text-sm font-medium">{t('sourceAssessments')}</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{JSON.stringify(report.source_assessments, null, 2)}</pre></details>
    <details className="border-t pt-3"><summary className="cursor-pointer text-sm font-medium">{t('configurationAssessments')}</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{JSON.stringify(report.configuration_assessments, null, 2)}</pre></details>
  </div>;
}

function CreateActionDialog({ open, onOpenChange, incident, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; incident: IncidentOverview; onCreated: () => Promise<void> }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const [actionType, setActionType] = useState<'mitigate' | 'remediate' | 'validate' | 'prevent'>('remediate');
  const [priority, setPriority] = useState<'P0' | 'P1' | 'P2' | 'P3'>('P2');
  const [title, setTitle] = useState('');
  const [rationale, setRationale] = useState('');
  const [validation, setValidation] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  async function create() {
    setSaving(true);
    try {
      await createIncidentAction(incident.id, { action_type: actionType, priority, title, rationale, validation, investigation_id: incident.investigations[0]?.id ?? null, evidence_refs: [] });
      await onCreated();
      onOpenChange(false);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setSaving(false);
    }
  }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('createAction')}</DialogTitle></DialogHeader><div className="grid gap-3 sm:grid-cols-2"><Select value={actionType} onChange={(event) => setActionType(event.target.value as typeof actionType)}><option value="mitigate">{t('actionTypeMitigate')}</option><option value="remediate">{t('actionTypeRemediate')}</option><option value="validate">{t('actionTypeValidate')}</option><option value="prevent">{t('actionTypePrevent')}</option></Select><Select value={priority} onChange={(event) => setPriority(event.target.value as typeof priority)}>{(['P0', 'P1', 'P2', 'P3'] as const).map((value) => <option key={value} value={value}>{value}</option>)}</Select><Input className="sm:col-span-2" placeholder={t('titleField')} value={title} onChange={(event) => setTitle(event.target.value)} /><Textarea className="sm:col-span-2" placeholder={t('rationale')} value={rationale} onChange={(event) => setRationale(event.target.value)} /><Textarea className="sm:col-span-2" placeholder={t('validation')} value={validation} onChange={(event) => setValidation(event.target.value)} /></div>{error && <p className="text-sm text-destructive">{error}</p>}<DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={!title || !rationale || !validation} onClick={() => void create()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>;
}
