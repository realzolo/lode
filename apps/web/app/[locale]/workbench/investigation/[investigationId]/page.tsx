'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Archive, ArrowLeft, CheckCircle2, CircleAlert, ClipboardCheck, RefreshCw, RotateCcw } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { Tabs } from '@/components/ui/tabs';
import { apiErrorMessage, archiveInvestigation, fetchInvestigation, fetchInvestigationAudit, fetchInvestigationTechnical, openInvestigationStream, retryInvestigation } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';
import type { InvestigationAuditItem, InvestigationAuditKind, InvestigationDetail, InvestigationOverview } from '@/lib/types';

const auditKinds: InvestigationAuditKind[] = ['access_decisions', 'read_attempts', 'ai_invocations', 'native_read_candidates', 'authorized_reads'];
const operationKindKeys = { model: 'operationKinds.model', source_read: 'operationKinds.source_read', native_read: 'operationKinds.native_read', snapshot: 'operationKinds.snapshot', validation: 'operationKinds.validation', synthesis: 'operationKinds.synthesis' } as const;
const evidenceKindKeys = { source_file: 'evidenceKinds.source_file', normalized_log_result: 'evidenceKinds.normalized_log_result', normalized_search_result: 'evidenceKinds.normalized_search_result', normalized_sql_result: 'evidenceKinds.normalized_sql_result', normalized_https_result: 'evidenceKinds.normalized_https_result', normalized_command_result: 'evidenceKinds.normalized_command_result' } as const;
const evidenceClassKeys = { runtime: 'evidenceClasses.runtime', incident_source: 'evidenceClasses.incident_source', repository_search_candidate: 'evidenceClasses.repository_search_candidate', runtime_identified: 'evidenceClasses.runtime_identified' } as const;

function statusClass(status: string) {
  return status === 'completed' || status === 'succeeded' || status === 'allowed' || status === 'authorized' ? 'success' : status === 'failed' || status === 'rejected' ? 'danger' : status === 'running' ? 'warning' : 'neutral';
}

function statusLabel(status: string, t: ReturnType<typeof useTranslations>) {
  const key: Record<string, string> = {
    queued: 'statusQueued',
    running: 'statusRunning',
    completed: 'statusCompleted',
    failed: 'statusFailed',
    pending: 'statusPending',
    succeeded: 'statusSucceeded',
    allowed: 'statusAllowed',
    rejected: 'statusRejected',
    authorized: 'statusAuthorized',
  };
  return key[status] ? t(key[status]) : status;
}

function resultStateLabel(resultState: string, t: ReturnType<typeof useTranslations>) {
  const key: Record<string, string> = {
    pending: 'resultPending',
    confirmed: 'resultConfirmed',
    hypothesis: 'resultHypothesis',
    insufficient: 'resultInsufficient',
    unavailable: 'resultUnavailable',
  };
  return key[resultState] ? t(key[resultState]) : resultState;
}

function severityLabel(severity: string | null, t: ReturnType<typeof useTranslations>) {
  if (severity === 'CRITICAL') return t('severityCritical');
  if (severity === 'WARNING') return t('severityWarning');
  return severity || '-';
}

export default function InvestigationPage({ params }: { params: { investigationId: string } }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const locale = useLocale();
  const router = useRouter();
  const [detail, setDetail] = useState<InvestigationOverview | null>(null);
  const [auditKind, setAuditKind] = useState<InvestigationAuditKind>('access_decisions');
  const [audit, setAudit] = useState<InvestigationAuditItem[]>([]);
  const [auditNext, setAuditNext] = useState<number | null>(null);
  const [technical, setTechnical] = useState<InvestigationDetail | null>(null);
  const [activeTab, setActiveTab] = useState('timeline');
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dateLocale = locale === 'zh' ? 'zh-CN' : 'en-US';
  const load = useCallback(async () => {
    try {
      const value = await fetchInvestigation(params.investigationId);
      setDetail(value);
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    }
  }, [params.investigationId]);
  const loadAudit = useCallback(async (append = false, afterId?: number) => {
    try {
      const page = await fetchInvestigationAudit(params.investigationId, auditKind, afterId);
      setAudit((current) => append ? [...current, ...page.items] : page.items);
      setAuditNext(page.next_after_id);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    }
  }, [auditKind, params.investigationId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (activeTab === 'audit') void loadAudit();
  }, [activeTab, loadAudit]);
  useEffect(() => {
    if (activeTab !== 'technical' || technical) return;
    void fetchInvestigationTechnical(params.investigationId).then(setTechnical).catch((cause) => setError(apiErrorMessage(cause, tc('requestFailed'))));
  }, [activeTab, params.investigationId, technical]);
  useEffect(() => {
    const close = openInvestigationStream(params.investigationId, 0, () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void load(), 150);
    });
    return () => { close(); if (timer.current) clearTimeout(timer.current); };
  }, [load, params.investigationId]);

  if (!detail) return <main className="p-8 text-sm text-muted-foreground">{error || tc('loading')}</main>;
  const investigation = detail;
  const terminal = investigation.status === 'completed' || investigation.status === 'failed';
  const report = investigation.report;
  const tabs = [
    { value: 'timeline', label: t('timeline', { count: detail.operation_count }), content: <Timeline items={detail.timeline} dateLocale={dateLocale} /> },
    { value: 'evidence', label: t('evidence', { count: detail.evidence_count }), content: <Evidence items={detail.evidence} dateLocale={dateLocale} /> },
    { value: 'audit', label: t('audit'), content: <Audit kind={auditKind} items={audit} nextAfterId={auditNext} onKindChange={setAuditKind} onMore={() => auditNext !== null && void loadAudit(true, auditNext)} dateLocale={dateLocale} /> },
    { value: 'technical', label: t('technical'), content: <Technical value={technical} /> },
  ];
  async function retry() {
    try {
      const created = await retryInvestigation(investigation.id);
      router.push(`/workbench/investigation/${created.id}`);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    }
  }
  async function archive() {
    try {
      await archiveInvestigation(investigation.id);
      await load();
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    }
  }
  return <main className="dashboard-page investigation-page space-y-6">
    <header className="border-b pb-6">
      <div className="mb-5 flex items-center justify-between">
        <Button size="sm" variant="ghost" asChild><Link href="/workbench"><ArrowLeft size={15} />{t('investigations')}</Link></Button>
        <div className="flex gap-2">
          {terminal && !detail.archived_at && <Button size="sm" variant="outline" onClick={() => void retry()}><RotateCcw size={15} />{tc('retry')}</Button>}
          {terminal && !detail.archived_at && <Button size="sm" variant="outline" onClick={() => void archive()}><Archive size={15} />{tc('archive')}</Button>}
          <Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
        </div>
      </div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-2 flex items-center gap-2"><span className={`table-status table-status-${statusClass(detail.status)}`}><i />{statusLabel(detail.status, t)}</span><span className="text-xs text-muted-foreground">{resultStateLabel(detail.result_state, t)}</span>{detail.archived_at && <span className="text-xs text-muted-foreground">{t('archived')}</span>}</div>
          <h1 className="max-w-4xl text-2xl font-semibold">{report?.headline || t('analysisInProgress')}</h1>
          <p className="mt-2 mono text-xs text-muted-foreground">{detail.id}</p>
        </div>
        <dl className="grid min-w-56 grid-cols-2 gap-x-5 gap-y-2 text-xs">
          <dt className="text-muted-foreground">{t('workspace')}</dt><dd>{detail.workspace_id}</dd>
          <dt className="text-muted-foreground">{t('event')}</dt><dd>{detail.event || '-'}</dd>
          <dt className="text-muted-foreground">{t('severity')}</dt><dd>{severityLabel(detail.severity, t)}</dd>
          <dt className="text-muted-foreground">{t('occurred')}</dt><dd>{detail.occurred_at ? new Date(detail.occurred_at).toLocaleString(dateLocale) : '-'}</dd>
        </dl>
      </div>
      {detail.error_message && <p className="mt-4 text-sm text-muted-foreground">{detail.error_type}: {detail.error_message}</p>}
      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}
    </header>
    {report ? <Report report={report} /> : <p className="border-y py-8 text-center text-muted-foreground">{t('noReport')}</p>}
    <Tabs items={tabs} onValueChange={setActiveTab} />
  </main>;
}

function Report({ report }: { report: NonNullable<InvestigationOverview['report']> }) {
  const t = useTranslations('workbench');
  return <section className="space-y-5 border-b pb-6">
    <div><p className="eyebrow">{t('summary')}</p><p className="mt-2 max-w-4xl text-base leading-7">{report.summary}</p></div>
    <div className="grid gap-4 md:grid-cols-2">
      <Insight title={t('likelyCause')} value={report.cause} icon={<CircleAlert size={17} />} detail={report.causal_chain} />
      <Insight title={t('codeDiagnosis')} value={report.diagnosis} icon={<ClipboardCheck size={17} />} />
    </div>
    <div className="grid gap-4 md:grid-cols-2">
      <ListPanel title={t('confirmedFacts')} values={report.confirmed_facts} positive />
      <ListPanel title={t('evidenceGaps')} values={report.evidence_gaps} />
    </div>
    <Insight title={t('recommendedNextStep')} value={report.next_step} icon={<CheckCircle2 size={17} />} />
  </section>;
}

function Insight({ title, value, icon, detail = [] }: { title: string; value: string; icon: React.ReactNode; detail?: string[] }) {
  return <section className="border p-4"><h2 className="flex items-center gap-2 text-sm font-semibold">{icon}{title}</h2><p className="mt-3 text-sm leading-6">{value || '-'}</p>{detail.length > 0 && <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">{detail.map((item) => <li key={item}>{item}</li>)}</ol>}</section>;
}

function ListPanel({ title, values, positive = false }: { title: string; values: string[]; positive?: boolean }) {
  return <section className="border p-4"><h2 className="text-sm font-semibold">{title}</h2>{values.length ? <ul className="mt-3 space-y-2 text-sm">{values.map((value) => <li key={value} className="flex gap-2"><span className={positive ? 'text-success' : 'text-warning'}>{positive ? '+' : '!'}</span><span>{value}</span></li>)}</ul> : <p className="mt-3 text-sm text-muted-foreground">-</p>}</section>;
}

function Timeline({ items, dateLocale }: { items: InvestigationOverview['timeline']; dateLocale: string }) {
  const t = useTranslations('workbench');
  return <div className="divide-y border-y">{items.map((item) => <article key={item.ordinal} className="grid gap-3 py-4 md:grid-cols-[48px_1fr_180px]"><div className="flex h-8 w-8 items-center justify-center rounded-sm border font-mono text-xs">{item.ordinal}</div><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{item.purpose}</h3><span className="text-xs text-muted-foreground">{t(operationKindKeys[item.kind as keyof typeof operationKindKeys])}</span></div><p className="mt-1 text-sm text-muted-foreground">{item.expected_evidence}</p>{item.failure_code && <p className="mt-2 text-xs text-destructive">{t('operationFailed')}</p>}</div><div className="text-xs text-muted-foreground md:text-right"><div>{statusLabel(item.status, t)}</div><div className="mt-1">{item.finished_at ? new Date(item.finished_at).toLocaleString(dateLocale) : '-'}</div></div></article>)}{items.length === 0 && <p className="py-8 text-center text-muted-foreground">{t('noTimeline')}</p>}</div>;
}

function Evidence({ items, dateLocale }: { items: InvestigationOverview['evidence']; dateLocale: string }) {
  const t = useTranslations('workbench');
  if (!items.length) return <p className="border-y py-8 text-center text-muted-foreground">{t('noEvidence')}</p>;
  return <div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('evidenceId')}</th><th>{t('evidenceKind')}</th><th>{t('evidenceClass')}</th><th>{t('sourceRevision')}</th><th>{t('time')}</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td>{item.id}</td><td>{t(evidenceKindKeys[item.kind as keyof typeof evidenceKindKeys])}</td><td>{t(evidenceClassKeys[item.evidence_class as keyof typeof evidenceClassKeys])}</td><td className="mono text-xs">{item.source_revision || '-'}</td><td className="text-xs">{item.source_time_start ? new Date(item.source_time_start).toLocaleString(dateLocale) : '-'}</td></tr>)}</tbody></table></div></div>;
}

function Audit({ kind, items, nextAfterId, onKindChange, onMore, dateLocale }: { kind: InvestigationAuditKind; items: InvestigationAuditItem[]; nextAfterId: number | null; onKindChange: (value: InvestigationAuditKind) => void; onMore: () => void; dateLocale: string }) {
  const t = useTranslations('workbench');
  const labels: Record<InvestigationAuditKind, string> = { native_read_candidates: t('auditCandidates'), access_decisions: t('auditDecisions'), authorized_reads: t('auditAuthorized'), read_attempts: t('auditAttempts'), ai_invocations: t('auditModels') };
  return <section className="space-y-4"><Select className="max-w-56" value={kind} onChange={(event) => onKindChange(event.target.value as InvestigationAuditKind)}>{auditKinds.map((value) => <option value={value} key={value}>{labels[value]}</option>)}</Select><div className="divide-y border-y">{items.map((item) => <article key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3"><div><p className="text-sm font-medium">{item.summary}</p><p className="mt-1 text-xs text-muted-foreground">#{item.id} · {new Date(item.created_at).toLocaleString(dateLocale)}</p></div><span className={`table-status table-status-${statusClass(item.status)}`}><i />{statusLabel(item.status, t)}</span></article>)}{items.length === 0 && <p className="py-8 text-center text-muted-foreground">{t('noAudit')}</p>}</div>{nextAfterId !== null && <Button variant="outline" onClick={onMore}>{t('loadMore')}</Button>}</section>;
}

function Technical({ value }: { value: InvestigationDetail | null }) {
  const t = useTranslations('workbench');
  if (!value) return <p className="border-y py-8 text-center text-muted-foreground">{t('technicalUnavailable')}</p>;
  return <pre className="max-h-[620px] overflow-auto border bg-card p-4 text-xs">{JSON.stringify(value, null, 2)}</pre>;
}
