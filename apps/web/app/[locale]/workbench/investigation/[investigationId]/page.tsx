'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  Copy,
  RefreshCw,
  RotateCcw,
} from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { InvestigationExecutionFlow } from '@/components/investigation-execution-flow';
import { Button } from '@/components/ui/button';
import {
  apiErrorMessage,
  archiveInvestigation,
  fetchInvestigation,
  fetchInvestigationExecutionGraph,
  openInvestigationStream,
  retryInvestigation,
} from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';
import type {
  InvestigationExecutionGraph,
  InvestigationOverview,
  InvestigationReportConclusion,
  InvestigationReportSummary,
} from '@/lib/types';

type FocusRequest = { nodeId: string; nonce: number } | null;

function statusClass(status: string) {
  if (['completed', 'succeeded', 'allowed', 'authorized'].includes(status)) return 'success';
  if (['failed', 'rejected', 'interrupted'].includes(status)) return 'danger';
  if (['running', 'reporting'].includes(status)) return 'warning';
  return 'neutral';
}

function statusLabel(status: string, t: ReturnType<typeof useTranslations>) {
  const key: Record<string, string> = {
    queued: 'statusQueued',
    running: 'statusRunning',
    reporting: 'statusReporting',
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
    no_defect: 'resultNoDefect',
    not_found: 'resultNotFound',
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
  const [executionGraph, setExecutionGraph] = useState<InvestigationExecutionGraph | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<FocusRequest>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [streamAfter, setStreamAfter] = useState<number | null>(null);
  const dateLocale = locale === 'zh' ? 'zh-CN' : 'en-US';

  const load = useCallback(async () => {
    try {
      const [value, graph] = await Promise.all([
        fetchInvestigation(params.investigationId),
        fetchInvestigationExecutionGraph(params.investigationId),
      ]);
      setDetail(value);
      setExecutionGraph(graph);
      setStreamAfter((current) => current ?? graph.event_cursor);
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    }
  }, [params.investigationId, tc]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (streamAfter === null) return;
    const close = openInvestigationStream(params.investigationId, streamAfter, () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void load(), 150);
    });
    return () => {
      close();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load, params.investigationId, streamAfter]);
  useEffect(() => {
    if (!detail || ['completed', 'failed'].includes(detail.status)) return;
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, 5_000);
    return () => window.clearInterval(interval);
  }, [detail?.status, load]);

  const evidenceNodeById = useMemo(() => {
    const values = new Map<number, string>();
    for (const node of executionGraph?.nodes ?? []) {
      for (const artifactId of node.evidence_refs) values.set(artifactId, node.id);
    }
    return values;
  }, [executionGraph]);

  const locateNode = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    setFocusRequest((current) => ({ nodeId, nonce: (current?.nonce ?? 0) + 1 }));
    window.requestAnimationFrame(() => {
      document.getElementById('investigation-flow')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }, []);

  const locateEvidence = useCallback((artifactId: number) => {
    const nodeId = evidenceNodeById.get(artifactId);
    if (nodeId) locateNode(nodeId);
  }, [evidenceNodeById, locateNode]);

  if (!detail) {
    return <main className="p-8 text-sm text-muted-foreground">{error || tc('loading')}</main>;
  }

  const investigation = detail;
  const terminal = ['completed', 'failed'].includes(investigation.status);

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

  async function copyInvestigationId() {
    try {
      await navigator.clipboard.writeText(String(investigation.id));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      setError(tc('requestFailed'));
    }
  }

  return <main className="dashboard-page investigation-page space-y-0">
    <header className="investigation-hero">
      <div className="investigation-hero-actions">
        <Button size="sm" variant="ghost" asChild>
          <Link href="/workbench"><ArrowLeft size={15} />{t('investigations')}</Link>
        </Button>
        <div className="flex gap-2">
          {terminal && !detail.archived_at && <Button size="sm" variant="outline" onClick={() => void retry()}><RotateCcw size={15} />{tc('retry')}</Button>}
          {terminal && !detail.archived_at && <Button size="sm" variant="outline" onClick={() => void archive()}><Archive size={15} />{tc('archive')}</Button>}
          <Button size="icon" variant="outline" aria-label={t('copyInvestigationId')} title={copied ? t('investigationIdCopied') : t('copyInvestigationId')} onClick={() => void copyInvestigationId()}>
            {copied ? <Check size={16} /> : <Copy size={16} />}
          </Button>
          <Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
        </div>
      </div>
      <div className="investigation-hero-heading">
        <div>
          <div className="investigation-state-line">
            <span className={`table-status table-status-${statusClass(detail.status)}`}><i />{statusLabel(detail.status, t)}</span>
            <span>{resultStateLabel(detail.result_state, t)}</span>
            {detail.archived_at && <span>{t('archived')}</span>}
          </div>
          <h1>{detail.report?.headline || detail.event || t('analysisInProgress')}</h1>
        </div>
        <dl className="investigation-core-meta">
          <div><dt>{t('severity')}</dt><dd>{severityLabel(detail.severity, t)}</dd></div>
          <div><dt>{t('event')}</dt><dd>{detail.event || '-'}</dd></div>
          <div><dt>{t('occurred')}</dt><dd>{detail.occurred_at ? new Date(detail.occurred_at).toLocaleString(dateLocale) : '-'}</dd></div>
        </dl>
      </div>
      {(detail.error_type || detail.error_message) && <div className="investigation-error-context" data-failed={detail.status === 'failed' ? 'true' : 'false'}>
        <AlertTriangle size={17} aria-hidden="true" />
        <div><strong>{detail.error_type && !['object', 'error'].includes(detail.error_type.toLowerCase()) ? detail.error_type : t('incidentError')}</strong><p>{detail.error_message || '-'}</p></div>
      </div>}
      {error && <p className="investigation-request-error">{error}</p>}
    </header>

    {detail.report
      ? <ReportSummary report={detail.report} evidenceNodeById={evidenceNodeById} onEvidenceSelect={locateEvidence} />
      : <InvestigationProgress
          status={detail.status}
          graph={executionGraph}
          operationCount={detail.operation_count}
          evidenceCount={detail.evidence_count}
          onStepSelect={locateNode}
        />}

    <InvestigationExecutionFlow
      investigationId={detail.id}
      graph={executionGraph}
      selectedNodeId={selectedNodeId}
      onSelectedNodeIdChange={setSelectedNodeId}
      focusRequest={focusRequest}
    />
  </main>;
}

function InvestigationProgress({
  status,
  graph,
  operationCount,
  evidenceCount,
  onStepSelect,
}: {
  status: string;
  graph: InvestigationExecutionGraph | null;
  operationCount: number;
  evidenceCount: number;
  onStepSelect: (nodeId: string) => void;
}) {
  const t = useTranslations('workbench');
  const failedNode = [...(graph?.nodes ?? [])].reverse().find((node) => ['failed', 'rejected', 'interrupted'].includes(node.status) && node.detail_available);
  return <section className="investigation-progress-summary">
    <RefreshCw size={18} aria-hidden="true" />
    <div>
      <p className="eyebrow">{t('flowCurrentPhase')}</p>
      <h2>{graph ? t(`flowPhase.${graph.phase}`) : t('flowLoading')}</h2>
      <p className="investigation-progress-counts">{t('investigationProgressCounts', { operations: operationCount, evidence: evidenceCount })}</p>
      {status === 'failed' && failedNode && <button type="button" className="investigation-failed-step" onClick={() => onStepSelect(failedNode.id)}>
        <AlertTriangle size={15} aria-hidden="true" />
        <span><strong>{t('failedStep')}</strong><small>{failedNode.title}{failedNode.failure_code ? ` · ${failedNode.failure_code}` : ''}</small></span>
        <ChevronRight size={15} aria-hidden="true" />
      </button>}
    </div>
  </section>;
}

function ReportSummary({
  report,
  evidenceNodeById,
  onEvidenceSelect,
}: {
  report: InvestigationReportSummary;
  evidenceNodeById: Map<number, string>;
  onEvidenceSelect: (artifactId: number) => void;
}) {
  const t = useTranslations('workbench');
  return <section className="investigation-report">
    <header>
      <p className="eyebrow">{t('summary')}</p>
      <p>{report.summary}</p>
    </header>
    <div className="investigation-report-grid">
      <div className="investigation-report-primary">
        <ConclusionHeading icon={<CircleAlert size={17} />} title={t('likelyCause')} conclusion={report.cause} />
        <p className="investigation-conclusion-text">{report.cause.summary || '-'}</p>
        <EvidenceReferences refs={report.cause.evidence_refs} evidenceNodeById={evidenceNodeById} onSelect={onEvidenceSelect} />
        {report.cause.causal_chain.length > 0 && <ol className="investigation-causal-chain">
          {report.cause.causal_chain.map((item, index) => <li key={`${index}-${item}`}><span>{index + 1}</span><p>{item}</p></li>)}
        </ol>}
        {report.confirmed_facts.length > 0 && <section className="investigation-facts">
          <h3><CheckCircle2 size={16} />{t('confirmedFacts')}</h3>
          <ul>{report.confirmed_facts.map((fact, index) => <li key={`${index}-${fact.text}`}>
            <Check size={14} aria-hidden="true" />
            <div><p>{fact.text}</p><EvidenceReferences refs={fact.evidence_refs} evidenceNodeById={evidenceNodeById} onSelect={onEvidenceSelect} /></div>
          </li>)}</ul>
        </section>}
      </div>
      <aside className="investigation-report-secondary">
        <section>
          <ConclusionHeading icon={<ClipboardCheck size={17} />} title={t('codeDiagnosis')} conclusion={report.code_diagnosis} />
          <p>{report.code_diagnosis.summary || '-'}</p>
          <EvidenceReferences refs={report.code_diagnosis.evidence_refs} evidenceNodeById={evidenceNodeById} onSelect={onEvidenceSelect} />
        </section>
        <section>
          <h3><CheckCircle2 size={16} />{t('recommendedNextStep')}</h3>
          <p>{report.next_step || '-'}</p>
        </section>
        {report.evidence_gaps.length > 0 && <section>
          <h3><AlertTriangle size={16} />{t('evidenceGaps')}</h3>
          <ul>{report.evidence_gaps.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>}
      </aside>
    </div>
  </section>;
}

function ConclusionHeading({ icon, title, conclusion }: { icon: React.ReactNode; title: string; conclusion: InvestigationReportConclusion }) {
  const t = useTranslations('workbench');
  return <div className="investigation-conclusion-heading">
    <h2>{icon}{title}</h2>
    <span>{resultStateLabel(conclusion.status, t)}</span>
  </div>;
}

function EvidenceReferences({
  refs,
  evidenceNodeById,
  onSelect,
}: {
  refs: number[];
  evidenceNodeById: Map<number, string>;
  onSelect: (artifactId: number) => void;
}) {
  const t = useTranslations('workbench');
  if (!refs.length) return null;
  return <div className="investigation-evidence-refs" aria-label={t('supportingEvidence')}>
    {refs.map((artifactId) => <button
      key={artifactId}
      type="button"
      disabled={!evidenceNodeById.has(artifactId)}
      title={evidenceNodeById.has(artifactId) ? t('locateEvidence') : t('evidenceSourceUnavailable')}
      onClick={() => onSelect(artifactId)}
    >{t('evidenceReference', { id: artifactId })}</button>)}
  </div>;
}
