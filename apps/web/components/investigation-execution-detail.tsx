'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Check,
  CircleDot,
  Clock3,
  Copy,
  Database,
  FileCode2,
  ListFilter,
  RefreshCw,
  Search,
  ShieldCheck,
} from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Select } from '@/components/ui/select';
import { TableColumns } from '@/components/ui/table';
import {
  apiErrorMessage,
  fetchInvestigationExecutionArtifact,
  fetchInvestigationExecutionNode,
} from '@/lib/api';
import type {
  InvestigationExecutionArtifactPage,
  InvestigationExecutionNode,
  InvestigationExecutionNodeDetail,
} from '@/lib/types';

type UnknownRecord = Record<string, unknown>;

const FIELD_KEYS = new Set([
  'provider',
  'status_code',
  'endpoint_id',
  'bytes',
  'output_bytes',
  'scanned_bytes',
  'exit_code',
  'duration_ms',
  'record_count',
  'result_type',
  'pages',
  'took_ms',
  'truncated',
  'prompt_injection_detected',
  'secret_categories',
  'path',
  'symbol',
  'start_line',
  'end_line',
  'source_type',
  'event',
  'severity',
  'occurred_at',
  'policy_outcome',
  'selected_operation_count',
  'attempt_count',
  'termination_reason',
  'working_set_id',
  'allowed_files',
]);
const EXECUTION_METRIC_KEYS = new Set([
  'bytes',
  'duration_ms',
  'output_bytes',
  'pages',
  'prompt_injection_detected',
  'record_count',
  'scanned_bytes',
  'took_ms',
  'truncated',
]);

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : null;
}

function asRecords(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item): item is UnknownRecord => item !== null) : [];
}

function textValue(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'bigint') return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return null;
}

function jsonRecord(value: unknown): UnknownRecord | null {
  if (typeof value !== 'string') return null;
  const raw = value.trim();
  const objectStart = raw.indexOf('{');
  const objectEnd = raw.lastIndexOf('}');
  if (objectStart < 0 || objectEnd <= objectStart) return null;
  try {
    return asRecord(JSON.parse(raw.slice(objectStart, objectEnd + 1)));
  } catch {
    return null;
  }
}

function formatDuration(duration: number | null) {
  if (duration === null) return '—';
  if (duration < 1_000) return `${duration} ms`;
  return `${(duration / 1_000).toFixed(duration < 10_000 ? 1 : 0)} s`;
}

function formatTimestamp(value: unknown, dateLocale: string) {
  const raw = textValue(value) || '';
  if (!raw) return '—';
  if (/^\d{16,}$/.test(raw)) {
    try {
      return new Date(Number(BigInt(raw) / 1_000_000n)).toLocaleString(dateLocale);
    } catch {
      return raw;
    }
  }
  const parsed = new Date(raw);
  return Number.isNaN(parsed.valueOf()) ? raw : parsed.toLocaleString(dateLocale);
}

function statusTone(status: string) {
  if (['succeeded', 'completed', 'allowed', 'authorized'].includes(status)) return 'success';
  if (['failed', 'rejected', 'interrupted'].includes(status)) return 'danger';
  if (status === 'running') return 'active';
  return 'neutral';
}

function statusLabel(status: string, t: ReturnType<typeof useTranslations>) {
  const labels: Record<string, string> = {
    queued: t('statusQueued'),
    running: t('statusRunning'),
    succeeded: t('statusSucceeded'),
    completed: t('statusCompleted'),
    failed: t('statusFailed'),
    rejected: t('statusRejected'),
    interrupted: t('statusInterrupted'),
    unavailable: t('statusUnavailable'),
    allowed: t('statusAllowed'),
    authorized: t('statusAuthorized'),
  };
  return labels[status] || status;
}

function nodeTypeLabel(type: InvestigationExecutionNode['node_type'], t: ReturnType<typeof useTranslations>) {
  const labels: Record<InvestigationExecutionNode['node_type'], string> = {
    input: t('flowNodeInput'),
    decision: t('flowNodeDecision'),
    operation: t('flowNodeOperation'),
    synthesis: t('flowNodeSynthesis'),
    verification: t('flowNodeVerification'),
    report: t('flowNodeReport'),
    phase: t('flowNodePhase'),
  };
  return labels[type];
}

export function NodeDetailDrawer({
  investigationId,
  node,
  eventCursor,
  onOpenChange,
}: {
  investigationId: number | string;
  node: InvestigationExecutionNode | null;
  eventCursor: number;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const locale = useLocale();
  const dateLocale = locale === 'zh' ? 'zh-CN' : 'en-US';
  const [detail, setDetail] = useState<InvestigationExecutionNodeDetail | null>(null);
  const [error, setError] = useState('');
  const [artifactId, setArtifactId] = useState<number | null>(null);
  const [page, setPage] = useState<InvestigationExecutionArtifactPage | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    if (!node) {
      setDetail(null);
      setArtifactId(null);
      setPage(null);
      return;
    }
    let current = true;
    setError('');
    void fetchInvestigationExecutionNode(investigationId, node.id)
      .then((value) => {
        if (!current) return;
        setDetail(value);
        setArtifactId((selected) => {
          const retained = selected && value.artifacts.some((artifact) => artifact.id === selected)
            ? selected
            : value.result_page?.artifact_id || value.artifacts[0]?.id || null;
          setPage((existing) => existing?.artifact_id === retained ? existing : value.result_page);
          return retained;
        });
      })
      .catch((cause) => current && setError(apiErrorMessage(cause, tc('requestFailed'))));
    return () => { current = false; };
  }, [eventCursor, investigationId, node?.id, tc]);

  const selectArtifact = useCallback(async (nextArtifactId: number) => {
    if (!node) return;
    setArtifactId(nextArtifactId);
    setPage(null);
    try {
      setPage(await fetchInvestigationExecutionArtifact(investigationId, node.id, nextArtifactId));
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    }
  }, [investigationId, node, tc]);

  const loadMore = useCallback(async () => {
    if (!node || !artifactId || page?.next_after_index === null || page?.next_after_index === undefined) return;
    setLoadingMore(true);
    try {
      const next = await fetchInvestigationExecutionArtifact(
        investigationId,
        node.id,
        artifactId,
        page.next_after_index,
      );
      setPage((current) => current ? { ...next, items: [...current.items, ...next.items] } : next);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setLoadingMore(false);
    }
  }, [artifactId, investigationId, node, page?.next_after_index, tc]);

  return <Dialog open={node !== null} onOpenChange={onOpenChange}>
    <DialogContent variant="drawer" className="execution-detail-drawer max-w-3xl gap-0 overflow-hidden p-0">
      <DialogHeader className="execution-detail-header">
        <DialogTitle>{node?.title || detail?.title || t('flowNodeDetails')}</DialogTitle>
        <DialogDescription>{node ? `${nodeTypeLabel(node.node_type, t)} · ${statusLabel(node.status, t)}` : t('flowDetailLoading')}</DialogDescription>
        {node && <div className="execution-detail-header-metrics">
          <span data-tone={statusTone(node.status)}>{statusLabel(node.status, t)}</span>
          <span><Clock3 size={13} />{formatDuration(node.duration_ms)}</span>
          {node.record_count !== null && <span>{t('flowRecords', { count: node.record_count })}</span>}
          {node.evidence_count > 0 && <span>{t('flowEvidenceCount', { count: node.evidence_count })}</span>}
        </div>}
      </DialogHeader>
      <div className="execution-detail-scroll">
        {error && <div className="dashboard-feedback execution-detail-error" role="alert"><AlertTriangle size={16} />{error}</div>}
        {!detail && !error && <div className="execution-detail-loading"><RefreshCw size={16} />{t('flowDetailLoading')}</div>}
        {detail && <NodeDetailContent
          detail={detail}
          page={page}
          artifactId={artifactId}
          dateLocale={dateLocale}
          loadingMore={loadingMore}
          onArtifactChange={selectArtifact}
          onLoadMore={loadMore}
        />}
      </div>
    </DialogContent>
  </Dialog>;
}

function NodeDetailContent({
  detail,
  page,
  artifactId,
  dateLocale,
  loadingMore,
  onArtifactChange,
  onLoadMore,
}: {
  detail: InvestigationExecutionNodeDetail;
  page: InvestigationExecutionArtifactPage | null;
  artifactId: number | null;
  dateLocale: string;
  loadingMore: boolean;
  onArtifactChange: (artifactId: number) => Promise<void>;
  onLoadMore: () => Promise<void>;
}) {
  const t = useTranslations('workbench');
  const authorization = asRecord(detail.authorization);
  const execution = asRecord(detail.execution);
  const failureCode = textValue(execution?.failure_code) || textValue(execution?.error_code) || textValue(authorization?.rejection_code);
  const failureDetail = textValue(execution?.failure_detail) || textValue(authorization?.rejection_detail);

  return <>
    {(failureCode || failureDetail) && <section className="execution-detail-failure-summary">
      <AlertTriangle size={17} aria-hidden="true" />
      <div><strong>{failureCode || t('flowFailure')}</strong><p>{failureDetail || t('operationFailed')}</p></div>
    </section>}
    <OverviewPresentation detail={detail} dateLocale={dateLocale} />
    {detail.query && <QueryPresentation query={detail.query} dateLocale={dateLocale} />}
    {detail.node_type === 'operation' && authorization && <AuthorizationPresentation authorization={authorization} dateLocale={dateLocale} />}
    {detail.artifacts.length > 0 && <DetailSection title={t('flowResponse')} icon={<Database size={16} />}>
      <div className="execution-artifact-heading">
        <EvidenceSummary artifact={detail.artifacts.find((artifact) => artifact.id === artifactId) || detail.artifacts[0]} dateLocale={dateLocale} />
        {detail.artifacts.length > 1 && <Select
          aria-label={t('flowSelectArtifact')}
          value={artifactId ? String(artifactId) : ''}
          onChange={(event) => void onArtifactChange(Number(event.target.value))}
        >{detail.artifacts.map((artifact) => <option key={artifact.id} value={artifact.id}>{artifactLabel(artifact.kind, t)} · #{artifact.id}</option>)}</Select>}
      </div>
      {page ? <ArtifactResult page={page} dateLocale={dateLocale} /> : <div className="execution-detail-loading">{t('flowResponseLoading')}</div>}
      {page?.item_truncated && <p className="execution-result-notice"><AlertTriangle size={14} />{t('flowResultTruncated')}</p>}
      {page && page.next_after_index !== null && <Button variant="outline" loading={loadingMore} onClick={() => void onLoadMore()}>{t('loadMore')}</Button>}
    </DetailSection>}
    <ExecutionPresentation execution={execution} artifactsAvailable={detail.artifacts.length > 0} dateLocale={dateLocale} />
    <AuditTimeline authorization={authorization} execution={execution} events={detail.events} dateLocale={dateLocale} />
  </>;
}

function OverviewPresentation({ detail, dateLocale }: { detail: InvestigationExecutionNodeDetail; dateLocale: string }) {
  const t = useTranslations('workbench');
  const overview = detail.overview;
  if (detail.node_type === 'input') {
    const error = asRecord(overview.error);
    const rawErrorType = textValue(error?.type);
    const errorType = rawErrorType && !['object', 'error'].includes(rawErrorType.toLowerCase())
      ? rawErrorType
      : t('incidentError');
    return <>
      <DetailSection title={t('flowIncidentContext')} icon={<CircleDot size={16} />}>
        <FactGrid items={[
          [t('summary'), overview.title],
          [t('severity'), overview.severity],
          [t('observed'), formatTimestamp(overview.observed_at, dateLocale)],
          [t('flowSourceType'), overview.source_type],
        ]} />
      </DetailSection>
      {error && <DetailSection title={t('incidentError')} icon={<AlertTriangle size={16} />}>
        <FactGrid items={[[t('errorType'), errorType], [t('errorMessage'), error.message]]} />
        {textValue(error.stack) && <CodeBlock value={textValue(error.stack) || ''} label={t('stackTrace')} />}
      </DetailSection>}
    </>;
  }
  if (detail.node_type === 'decision') {
    return <>
      <DetailText title={t('flowDecisionOutcome')} value={overview.decision} />
      <DetailSection title={t('flowDecisionContext')} icon={<ListFilter size={16} />}>
        <FactGrid items={[
          [t('flowRoundLabel'), overview.ordinal],
          [t('flowPolicyOutcome'), overview.policy_outcome],
          [t('flowSelectedOperations'), overview.selected_operation_count],
        ]} />
        <StructuredValue value={overview.hypotheses} emptyLabel={t('flowNoHypotheses')} />
      </DetailSection>
    </>;
  }
  if (detail.node_type === 'operation') {
    return <>
      <DetailText title={t('flowPurpose')} value={overview.purpose} />
      <DetailText title={t('flowSelectionReason')} value={overview.selection_reason} />
      <DetailText title={t('flowExpectedEvidence')} value={overview.expected_evidence} />
      {textValue(overview.stop_condition) && <DetailText title={t('flowStopCondition')} value={overview.stop_condition} />}
      {textValue(overview.operation_kind) === 'source_read' && <SourceRequest value={overview.input_masked} />}
    </>;
  }
  if (detail.node_type === 'report') {
    const cause = asRecord(overview.incident_cause);
    const diagnosis = asRecord(overview.code_diagnosis);
    return <>
      <DetailText title={t('summary')} value={overview.summary} />
      <DetailText title={t('likelyCause')} value={cause?.mechanism} />
      <DetailText title={t('codeDiagnosis')} value={diagnosis?.summary} />
      <DetailText title={t('recommendedNextStep')} value={overview.next_step} />
      {Array.isArray(overview.evidence_gaps) && overview.evidence_gaps.length > 0 && <DetailSection title={t('evidenceGaps')} icon={<AlertTriangle size={16} />}>
        <StructuredValue value={overview.evidence_gaps} />
      </DetailSection>}
    </>;
  }
  return <DetailSection title={t('flowAnalysisStage')} icon={<ShieldCheck size={16} />}>
    <FactGrid items={[[t('flowRole'), overview.role], [t('flowVerificationVerdict'), overview.verdict]]} />
    {Array.isArray(overview.reasons) && overview.reasons.length > 0 && <div className="execution-verification-reasons">
      <h4>{t('flowVerificationReasons')}</h4>
      <StructuredValue value={overview.reasons} />
    </div>}
  </DetailSection>;
}

function QueryPresentation({ query, dateLocale }: { query: UnknownRecord; dateLocale: string }) {
  const t = useTranslations('workbench');
  const language = textValue(query.language) || '';
  const state = textValue(query.state) || 'proposed';
  const effective = asRecord(query.effective_action);
  const proposed = asRecord(query.proposed_payload);
  const action = effective || proposed;
  if (!action) return null;
  return <DetailSection title={state === 'proposed' ? t('flowProposedQuery') : t('flowActualQuery')} icon={<Search size={16} />}>
    <FactGrid items={[
      [t('flowQueryLanguage'), language || '—'],
      [t('flowQueryState'), t(`flowQueryStates.${state}`)],
      [t('flowQueryLimit'), query.requested_limit],
      [t('flowQueryTimeout'), query.requested_timeout_ms ? `${query.requested_timeout_ms} ms` : null],
    ]} />
    <TimeWindow value={query.requested_window} dateLocale={dateLocale} />
    <QueryAction language={language} action={action} />
    {state === 'proposed' && <p className="execution-result-notice"><AlertTriangle size={14} />{t('flowProposedNotExecuted')}</p>}
  </DetailSection>;
}

function QueryAction({ language, action }: { language: string; action: UnknownRecord }) {
  const t = useTranslations('workbench');
  if (language === 'sql') {
    return <CodeBlock value={textValue(action.query) || ''} label={textValue(action.dialect) || 'SQL'} />;
  }
  if (language === 'logql') {
    const queries = Array.isArray(action.queries) ? action.queries.map(textValue).filter((item): item is string => Boolean(item)) : [];
    return <div className="execution-query-list">{queries.map((query, index) => <CodeBlock key={`${index}-${query}`} value={query} label={queries.length > 1 ? t('flowQueryBranch', { index: index + 1 }) : 'LogQL'} />)}</div>;
  }
  if (language.includes('search') || language.includes('elasticsearch') || language.includes('opensearch')) {
    return <SearchAction action={action} />;
  }
  if (language === 'https') {
    return <HttpsAction action={action} />;
  }
  if (language === 'command') {
    return <CommandAction action={action} />;
  }
  const query = textValue(action.query);
  return query ? <CodeBlock value={query} label={language || t('flowQuery')} /> : <StructuredValue value={action} />;
}

function SearchAction({ action }: { action: UnknownRecord }) {
  const t = useTranslations('workbench');
  const body = asRecord(action.body);
  const clauses = useMemo(() => collectSearchClauses(body?.query), [body?.query]);
  const aggregations = asRecord(body?.aggregations) || asRecord(body?.aggs);
  return <div className="execution-search-query">
    <FactGrid items={[[t('flowSearchPath'), action.path], [t('flowSearchPageSize'), action.page_size]]} />
    {clauses.length > 0 && <div><h4>{t('flowSearchConditions')}</h4><ul>{clauses.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>}
    {body?.sort !== undefined && <div><h4>{t('flowSearchSort')}</h4><StructuredValue value={body.sort} /></div>}
    {aggregations && <div><h4>{t('flowSearchAggregations')}</h4><StructuredValue value={aggregations} /></div>}
  </div>;
}

function collectSearchClauses(value: unknown, path = ''): string[] {
  const record = asRecord(value);
  if (!record) return textValue(value) ? [`${path}: ${textValue(value)}`] : [];
  const clauses: string[] = [];
  for (const [key, child] of Object.entries(record)) {
    const nextPath = path ? `${path} · ${humanizeKey(key)}` : humanizeKey(key);
    if (Array.isArray(child)) {
      child.forEach((item) => clauses.push(...collectSearchClauses(item, nextPath)));
    } else if (asRecord(child)) {
      clauses.push(...collectSearchClauses(child, nextPath));
    } else if (textValue(child) !== null) {
      clauses.push(`${nextPath}: ${textValue(child)}`);
    }
  }
  return clauses.slice(0, 24);
}

function HttpsAction({ action }: { action: UnknownRecord }) {
  const t = useTranslations('workbench');
  const method = textValue(action.method) || 'GET';
  const target = `${textValue(action.origin) || ''}${textValue(action.path) || ''}`;
  return <div className="execution-http-query">
    <div className="execution-http-target"><strong>{method}</strong><code>{target}</code></div>
    {asRecord(action.query) && <><h4>{t('flowRequestParameters')}</h4><StructuredValue value={action.query} /></>}
  </div>;
}

function CommandAction({ action }: { action: UnknownRecord }) {
  const t = useTranslations('workbench');
  const argv = Array.isArray(action.argv) ? action.argv.map(textValue).filter((item): item is string => item !== null) : [];
  const patternIndex = typeof action.pattern_index === 'number' ? action.pattern_index : -1;
  return <div className="execution-command-query">
    <FactGrid items={[[t('flowSearchPattern'), argv[patternIndex]], [t('flowWorkingSet'), action.working_set_id]]} />
    <h4>{t('flowAllowedFiles')}</h4>
    <StructuredValue value={action.allowed_files} />
  </div>;
}

function AuthorizationPresentation({ authorization, dateLocale }: { authorization: UnknownRecord; dateLocale: string }) {
  const t = useTranslations('workbench');
  const outcome = textValue(authorization.outcome) || '—';
  const budget = asRecord(authorization.effective_budget);
  return <DetailSection title={t('flowAuthorization')} icon={<ShieldCheck size={16} />}>
    <div className="execution-authorization-summary" data-tone={statusTone(outcome === 'allow' ? 'allowed' : outcome)}>
      <ShieldCheck size={16} /><strong>{outcome === 'allow' ? t('flowAuthorizationAllowed') : t('flowAuthorizationRejected')}</strong>
    </div>
    {budget && <FactGrid items={[
      [t('flowWindowStart'), formatTimestamp(budget.window_start, dateLocale)],
      [t('flowWindowEnd'), formatTimestamp(budget.window_end, dateLocale)],
      [t('flowQueryLimit'), budget.result_limit],
      [t('flowQueryTimeout'), budget.timeout_ms ? `${budget.timeout_ms} ms` : null],
    ]} />}
    {textValue(authorization.rejection_code) && <FactGrid items={[[t('flowFailureCode'), authorization.rejection_code], [t('flowFailureReason'), authorization.rejection_detail]]} />}
  </DetailSection>;
}

function ExecutionPresentation({ execution, artifactsAvailable, dateLocale }: { execution: UnknownRecord | null; artifactsAvailable: boolean; dateLocale: string }) {
  const t = useTranslations('workbench');
  if (!execution) return null;
  const metrics = asRecord(execution.metrics);
  const result = execution.result;
  const attempts = asRecords(execution.attempts);
  const isModelInvocation = textValue(execution.role) !== null;
  const hasSummary = isModelInvocation || metrics || (!artifactsAvailable && result !== null && result !== undefined) || attempts.length > 0;
  if (!hasSummary) return null;
  return <DetailSection title={t('flowExecution')} icon={<Clock3 size={16} />}>
    <FactGrid items={[
      [t('flowStartedAt'), formatTimestamp(execution.started_at || execution.created_at, dateLocale)],
      [t('flowFinishedAt'), execution.finished_at ? formatTimestamp(execution.finished_at, dateLocale) : null],
      [fieldLabel('duration_ms', t), execution.latency_ms !== undefined ? `${execution.latency_ms} ms` : null],
      [fieldLabel('attempt_count', t), execution.attempt_count],
      [fieldLabel('termination_reason', t), execution.termination_reason],
      ...Object.entries(metrics || {})
        .filter(([key, value]) => EXECUTION_METRIC_KEYS.has(key) && textValue(value) !== null)
        .slice(0, 6)
        .map(([key, value]) => [fieldLabel(key, t), metricValue(key, value)] as [string, unknown]),
    ]} />
    {!artifactsAvailable && result !== null && result !== undefined && <StructuredValue value={result} />}
    {attempts.length > 0 && <div className="execution-attempts"><h4>{t('flowAttempts')}</h4>{attempts.map((attempt, index) => <div key={textValue(attempt.id) || index}>
      <strong>{t('flowAttemptNumber', { number: textValue(attempt.attempt) || index + 1 })}</strong>
      <span data-tone={statusTone(textValue(attempt.status) || '')}>{statusLabel(textValue(attempt.status) || '—', t)}</span>
      {textValue(attempt.failure_code) && <small>{textValue(attempt.failure_code)}</small>}
    </div>)}</div>}
  </DetailSection>;
}

function AuditTimeline({ authorization, execution, events, dateLocale }: { authorization: UnknownRecord | null; execution: UnknownRecord | null; events: UnknownRecord[]; dateLocale: string }) {
  const t = useTranslations('workbench');
  const attempts = asRecords(execution?.attempts);
  if (!authorization && !attempts.length && !events.length) return null;
  return <DetailSection title={t('flowExecutionRecord')} icon={<CircleDot size={16} />}>
    <ol className="execution-audit-chain">
      {authorization && <li><ShieldCheck size={15} /><div><strong>{authorization.outcome === 'allow' ? t('flowAuthorizationAllowed') : t('flowAuthorizationRejected')}</strong>{textValue(authorization.rejection_code) && <small>{textValue(authorization.rejection_code)}</small>}</div></li>}
      {attempts.map((attempt, index) => <li key={textValue(attempt.id) || index}><Clock3 size={15} /><div><strong>{t('flowAttemptStatus', { number: textValue(attempt.attempt) || index + 1, status: statusLabel(textValue(attempt.status) || '—', t) })}</strong><small>{formatTimestamp(attempt.finished_at || attempt.started_at, dateLocale)}</small></div></li>)}
      {events.map((event, index) => <li key={textValue(event.sequence) || index}><CircleDot size={15} /><div><strong>{textValue(event.message) || textValue(event.event_name) || t('flowExecutionEvent')}</strong><small>{formatTimestamp(event.occurred_at, dateLocale)}</small></div></li>)}
    </ol>
  </DetailSection>;
}

function ArtifactResult({ page, dateLocale }: { page: InvestigationExecutionArtifactPage; dateLocale: string }) {
  const t = useTranslations('workbench');
  const rows = page.items.map(asRecord).filter((item): item is UnknownRecord => item !== null);
  if (page.artifact_kind === 'source_file') return <SourceResult page={page} />;
  if (page.artifact_kind.includes('sql') && rows.length) return <RecordTable rows={rows} />;
  if (page.artifact_kind.includes('log') && rows.length) return <LogResult rows={rows} dateLocale={dateLocale} />;
  if (page.artifact_kind.includes('search') && rows.length) {
    return <div className="execution-search-result">
      {asRecord(page.metadata.aggregations) && <div><h4>{t('flowSearchAggregations')}</h4><StructuredValue value={page.metadata.aggregations} /></div>}
      <RecordTable rows={rows.map(flattenSearchRecord)} />
    </div>;
  }
  if (page.artifact_kind.includes('command')) return <CommandResult page={page} />;
  if (page.artifact_kind.includes('https')) return <HttpsResult page={page} />;
  return <div className="execution-structured-result">
    {Object.keys(page.metadata).length > 0 && <StructuredValue value={page.metadata} />}
    <StructuredValue value={page.items} emptyLabel={t('flowNoResults')} />
  </div>;
}

function LogResult({ rows, dateLocale }: { rows: UnknownRecord[]; dateLocale: string }) {
  const t = useTranslations('workbench');
  const ordered = [...rows].sort((left, right) => (textValue(left.timestamp || left.time) || '').localeCompare(textValue(right.timestamp || right.time) || ''));
  return <ol className="execution-log-list">{ordered.map((row, index) => {
    const labels = asRecord(row.labels) || {};
    const rawMessage = textValue(row.message) || textValue(row.line) || textValue(row.value);
    const payload = jsonRecord(rawMessage);
    const nestedDetail = jsonRecord(payload?.detail);
    const level = textValue(payload?.level) || textValue(row.level) || textValue(labels.level) || textValue(labels.severity);
    const message = textValue(payload?.message) || rawMessage || t('flowLogEntry');
    const fields = {
      ...labels,
      ...Object.fromEntries(Object.entries(payload || {}).filter(([key]) => !['level', 'message', 'timestamp', 'detail'].includes(key))),
      ...(nestedDetail || {}),
    };
    return <li key={index}>
      <div><time>{formatTimestamp(payload?.timestamp || row.timestamp || row.time, dateLocale)}</time>{level && <span data-level={level.toLowerCase()}>{level}</span>}</div>
      <code>{message}</code>
      {Object.keys(fields).length > 0 && <div className="execution-log-labels">{Object.entries(fields).slice(0, 12).map(([key, value]) => <span key={key}><b>{humanizeKey(key)}</b>{textValue(value) || '—'}</span>)}</div>}
    </li>;
  })}</ol>;
}

function SourceResult({ page }: { page: InvestigationExecutionArtifactPage }) {
  const t = useTranslations('workbench');
  const source = asRecord(page.items[0]);
  if (!source) return <p className="execution-empty-result">{t('flowNoResults')}</p>;
  const lineRange = source.start_line || source.end_line
    ? `${textValue(source.start_line) || '?'}-${textValue(source.end_line) || '?'}`
    : '—';
  return <div className="execution-source-result">
    <FactGrid items={[[t('flowSourcePath'), source.path], [t('flowSourceSymbol'), source.symbol], [t('flowSourceLines'), lineRange]]} />
    <CodeBlock value={textValue(source.content) || ''} label={textValue(source.path) || t('flowSourceCode')} />
  </div>;
}

function CommandResult({ page }: { page: InvestigationExecutionArtifactPage }) {
  const t = useTranslations('workbench');
  const lines = page.items.map(textValue).filter((item): item is string => item !== null);
  return <div className="execution-command-result">
    <FactGrid items={[[fieldLabel('exit_code', t), page.metadata.exit_code], [fieldLabel('duration_ms', t), page.metadata.duration_ms], [fieldLabel('bytes', t), page.metadata.bytes]]} />
    <ol>{lines.map((line, index) => <li key={`${index}-${line}`}><code>{line}</code></li>)}</ol>
    {textValue(page.metadata.stderr) && <div className="execution-command-stderr"><strong>{t('flowStandardError')}</strong><code>{textValue(page.metadata.stderr)}</code></div>}
    {!lines.length && !textValue(page.metadata.stderr) && <p className="execution-empty-result">{t('flowNoResults')}</p>}
  </div>;
}

function HttpsResult({ page }: { page: InvestigationExecutionArtifactPage }) {
  const t = useTranslations('workbench');
  const response = asRecord(page.items[0]);
  if (!response) return <p className="execution-empty-result">{t('flowNoResults')}</p>;
  const record = response.record;
  return <div className="execution-http-result">
    <FactGrid items={[[t('flowProvider'), response.provider], [t('flowEndpoint'), response.endpoint_id], [fieldLabel('status_code', t), response.status_code], [t('flowResponseBytes'), response.bytes]]} />
    <StructuredValue value={record} emptyLabel={t('flowNoResults')} />
  </div>;
}

function flattenSearchRecord(row: UnknownRecord): UnknownRecord {
  const source = asRecord(row._source);
  if (!source) return row;
  const flattened: UnknownRecord = {};
  for (const [key, value] of Object.entries(row)) if (key !== '_source') flattened[key] = value;
  for (const [key, value] of Object.entries(source)) flattened[key] = value;
  return flattened;
}

function RecordTable({ rows }: { rows: UnknownRecord[] }) {
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 16);
  return <div className="execution-result-table table-wrap"><table className="table"><TableColumns widths={columns.map(() => 1)} /><thead><tr>{columns.map((column) => <th key={column}>{humanizeKey(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}><CellValue value={row[column]} /></td>)}</tr>)}</tbody></table></div>;
}

function CellValue({ value }: { value: unknown }) {
  const t = useTranslations('workbench');
  const scalar = textValue(value);
  if (scalar !== null) return <>{scalar}</>;
  if (Array.isArray(value)) return <span className="text-muted-foreground">{t('flowItemsCount', { count: value.length })}</span>;
  const record = asRecord(value);
  if (record) return <span className="text-muted-foreground">{t('flowFieldsCount', { count: Object.keys(record).length })}</span>;
  return <span className="text-muted-foreground">—</span>;
}

function StructuredValue({ value, emptyLabel }: { value: unknown; emptyLabel?: string }) {
  const t = useTranslations('workbench');
  const scalar = textValue(value);
  if (scalar !== null) return <p className="execution-structured-scalar">{scalar}</p>;
  if (Array.isArray(value)) {
    if (!value.length) return <p className="execution-empty-result">{emptyLabel || '—'}</p>;
    const rows = asRecords(value);
    if (rows.length === value.length) return <RecordTable rows={rows} />;
    return <ul className="execution-structured-list">{value.slice(0, 50).map((item, index) => <li key={index}><CellValue value={item} /></li>)}</ul>;
  }
  const record = asRecord(value);
  if (!record || !Object.keys(record).length) return <p className="execution-empty-result">{emptyLabel || '—'}</p>;
  const entries = Object.entries(record).slice(0, 24);
  return <dl className="execution-structured-fields">{entries.map(([key, item]) => <div key={key}>
    <dt>{fieldLabel(key, t)}</dt>
    <dd><CellValue value={item} /></dd>
  </div>)}</dl>;
}

function DetailSection({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return <section className="execution-detail-section"><h3>{icon}{title}</h3>{children}</section>;
}

function DetailText({ title, value }: { title: string; value: unknown }) {
  const text = textValue(value);
  if (!text) return null;
  return <section className="execution-detail-section"><h3>{title}</h3><p>{text}</p></section>;
}

function FactGrid({ items }: { items: Array<[string, unknown]> }) {
  const values = items.filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (!values.length) return null;
  return <dl className="execution-detail-facts">{values.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><CellValue value={value} /></dd></div>)}</dl>;
}

function TimeWindow({ value, dateLocale }: { value: unknown; dateLocale: string }) {
  const t = useTranslations('workbench');
  const window = asRecord(value);
  if (!window) return null;
  return <FactGrid items={[
    [t('flowWindowStart'), formatTimestamp(window.start || window.window_start, dateLocale)],
    [t('flowWindowEnd'), formatTimestamp(window.end || window.window_end, dateLocale)],
  ]} />;
}

function SourceRequest({ value }: { value: unknown }) {
  const t = useTranslations('workbench');
  const input = asRecord(value);
  if (!input) return null;
  return <DetailSection title={t('flowSourceRequest')} icon={<FileCode2 size={16} />}>
    <FactGrid items={[[t('flowSourcePath'), input.path], [t('flowSourceSymbol'), input.symbol], [t('flowSourceRevision'), input.revision || input.sha]]} />
  </DetailSection>;
}

function CodeBlock({ value, label }: { value: string; label: string }) {
  const t = useTranslations('workbench');
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  async function copy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_200);
  }
  return <div className="execution-code-block">
    <div><span>{label}</span><Button size="icon" variant="ghost" aria-label={t('flowCopyCode')} title={t('flowCopyCode')} onClick={() => void copy()}>{copied ? <Check size={14} /> : <Copy size={14} />}</Button></div>
    <pre><code>{value}</code></pre>
  </div>;
}

function EvidenceSummary({ artifact, dateLocale }: { artifact: InvestigationExecutionNodeDetail['artifacts'][number]; dateLocale: string }) {
  const t = useTranslations('workbench');
  return <div className="execution-evidence-summary">
    <strong>{t('evidenceReference', { id: artifact.id })}</strong>
    <span>{artifactLabel(artifact.kind, t)} · {artifact.record_count === null ? '—' : t('flowRecords', { count: artifact.record_count })} · {formatTimestamp(artifact.archived_at, dateLocale)}</span>
  </div>;
}

function artifactLabel(kind: string, t: ReturnType<typeof useTranslations>) {
  const labels: Record<string, string> = {
    source_file: t('evidenceKinds.source_file'),
    normalized_log_result: t('evidenceKinds.normalized_log_result'),
    normalized_search_result: t('evidenceKinds.normalized_search_result'),
    normalized_sql_result: t('evidenceKinds.normalized_sql_result'),
    normalized_https_result: t('evidenceKinds.normalized_https_result'),
    normalized_command_result: t('evidenceKinds.normalized_command_result'),
  };
  return labels[kind] || humanizeKey(kind);
}

function fieldLabel(key: string, t: ReturnType<typeof useTranslations>) {
  return FIELD_KEYS.has(key) ? t(`flowFields.${key}`) : humanizeKey(key);
}

function metricValue(key: string, value: unknown) {
  const text = textValue(value);
  if (text === null) return value;
  return key.endsWith('_ms') ? `${text} ms` : text;
}

function humanizeKey(key: string) {
  return key.replace(/^_+/, '').replace(/[._-]+/g, ' ').replace(/\b\w/g, (value) => value.toUpperCase());
}
