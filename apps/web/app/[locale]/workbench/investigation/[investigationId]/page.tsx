'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Archive, CheckCircle2, ChevronRight, Clock3, FileCode2, LockKeyhole, RefreshCw, RotateCcw, ShieldAlert, SlidersHorizontal, X } from 'lucide-react';
import { InvestigationCodeViewer } from '@/components/investigation-code-viewer';
import { InvestigationWorkflow } from '@/components/investigation-workflow';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { archiveInvestigation, fetchInvestigation, fetchInvestigationAudit, openInvestigationStream, retryInvestigation, type InvestigationCodeFinding, type InvestigationDetail, type InvestigationEvidence } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';

const STATE_LABELS = { pending: '调查中', confirmed: '已确认', hypothesis: '待验证假设', insufficient: '证据不足', unavailable: '分析不可用' } as const;
const INCIDENT_STATUS_LABELS: Record<string, string> = { confirmed: '原因已确认', hypothesis: '原因待验证', not_found: '未定位原因' };
const CODE_STATUS_LABELS: Record<InvestigationCodeFinding['status'], string> = { confirmed: '已确认代码缺陷', hypothesis: '待验证代码假设', no_defect: '未发现代码缺陷', not_found: '未定位到代码' };

function hasCodeLocation(finding: InvestigationCodeFinding) {
  return Boolean(finding.artifact_id && finding.path && finding.start_line && finding.end_line);
}

function formatTime(value: string | null | undefined) {
  return value ? new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)) : '未提供';
}

function InvestigationLoading() {
  return <main className="investigation-page investigation-workbench serial-investigation-page investigation-page-shell-loading" aria-busy="true"><header className="investigation-header serial-header"><Skeleton className="h-3 w-64" /><div className="investigation-loading-title"><Skeleton className="h-9 w-9" variant="squared" /><div><Skeleton className="h-6 w-80" /><Skeleton className="mt-2 h-4 w-[520px]" /></div></div><Skeleton className="mt-4 h-4 w-96" /></header>{Array.from({ length: 3 }).map((_, index) => <section key={index} className="investigation-loading-band"><Skeleton className="h-3 w-28" /><Skeleton className="mt-3 h-6 w-2/3" /><Skeleton className="mt-4 h-4 w-full" /><Skeleton className="mt-2 h-4 w-4/5" /></section>)}</main>;
}

export default function InvestigationPage({ params }: { params: { investigationId: string } }) {
  const router = useRouter();
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null);
  const [selectedFindingId, setSelectedFindingId] = useState<number | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cursor = useRef(0);
  const load = useCallback(async () => {
    try {
      const data = await fetchInvestigation(params.investigationId);
      setDetail(data);
      cursor.current = Math.max(cursor.current, data.event_cursor);
      setSelectedOperationId((value) => value || data.operations.find((item) => item.status === 'running')?.id || data.operations.at(-1)?.id || null);
      setSelectedFindingId((value) => {
        const stillValid = data.code_findings.some((item) => item.id === value && hasCodeLocation(item));
        return stillValid ? value : data.code_findings.find(hasCodeLocation)?.id || null;
      });
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [params.investigationId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const close = openInvestigationStream(params.investigationId, cursor.current, {
      onEvent: (event) => {
        if (event.sequence) cursor.current = Math.max(cursor.current, event.sequence);
        if (refreshTimer.current) clearTimeout(refreshTimer.current);
        refreshTimer.current = setTimeout(() => void load(), 120);
      },
      onClose: () => void load(),
      onError: () => { refreshTimer.current = setTimeout(() => void load(), 1_500); },
    });
    return () => { close(); if (refreshTimer.current) clearTimeout(refreshTimer.current); };
  }, [load, params.investigationId]);

  const locatableFindings = detail?.code_findings.filter(hasCodeLocation) || [];
  const selectedFinding = locatableFindings.find((item) => item.id === selectedFindingId) || locatableFindings[0] || null;
  const diagnosisFinding = selectedFinding || detail?.code_findings[0] || null;
  const selectedEvidence = detail?.evidence.find((item) => item.id === selectedFinding?.artifact_id) || null;
  if (loading) return <InvestigationLoading />;
  if (!detail) return <div className="investigation-loading investigation-error">{error || '调查不存在'}<Button variant="outline" size="sm" onClick={() => void load()}>重试</Button></div>;

  const currentStep = detail.steps.find((item) => item.status === 'running');
  const report = detail.report;
  const terminal = detail.status === 'completed' || detail.status === 'failed';
  const investigationId = detail.id;
  async function handleRetry() {
    setActionBusy(true);
    setActionError(null);
    try {
      const retried = await retryInvestigation(investigationId);
      router.push(`/workbench/investigation/${retried.id}`);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(false);
    }
  }
  async function handleArchive() {
    setActionBusy(true);
    setActionError(null);
    try {
      await archiveInvestigation(investigationId);
      setArchiveOpen(false);
      await load();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(false);
    }
  }
  return <main className="investigation-page investigation-workbench serial-investigation-page">
    {error && <div className="investigation-sync-warning" role="status">实时同步暂时中断，正在保留当前结果并自动重连。</div>}
    <header className="investigation-header serial-header">
      <div className="investigation-breadcrumb"><Link href="/workbench">调查</Link><ChevronRight size={14} /><span>{detail.application_name}</span><ChevronRight size={14} /><span className="mono">{detail.id.slice(0, 12)}</span></div>
      <div className="investigation-header-row"><div className="investigation-title"><div className={`investigation-severity investigation-severity-${detail.input?.severity === 'CRITICAL' ? 'critical' : 'warning'}`}><AlertTriangle size={18} /></div><div><h1>{detail.input?.title || '事故调查'}</h1><p>{detail.input?.error.name}: {detail.input?.error.message}</p></div></div><div className="investigation-actions">{terminal && !detail.archived_at && <Button size="sm" variant="outline" onClick={() => void handleRetry()} disabled={actionBusy}><RotateCcw size={14} />重试</Button>}{terminal && !detail.archived_at && <Button size="sm" variant="outline" onClick={() => setArchiveOpen(true)} disabled={actionBusy}><Archive size={14} />归档</Button>}<Button size="icon" variant="outline" onClick={() => void load()} aria-label="刷新"><RefreshCw size={15} /></Button><Button size="sm" variant="outline" onClick={() => setAuditOpen(true)}><SlidersHorizontal size={14} />审计</Button></div></div>
      <div className="investigation-meta"><span className={`result-state result-state-${detail.result_state}`}>{STATE_LABELS[detail.result_state]}</span>{detail.archived_at && <span className="result-state archived-state"><LockKeyhole size={12} />已归档 · 只读</span>}<span><Clock3 size={13} />{formatTime(detail.input?.occurred_at)}</span><span>{detail.scope.deployment_sha ? `部署 ${detail.scope.deployment_sha.slice(0, 12)}` : '未提供事故部署版本'}</span>{currentStep && <span className="live-step-label">当前步骤：{currentStep.title}</span>}</div>
      {actionError && <p className="investigation-action-error">{actionError}</p>}
    </header>

    <section className="cause-band">
      <header><div><span>事故真实原因</span><h2>{report?.headline || '等待证据归因'}</h2></div><span className={`result-state result-state-${report?.incident_cause.status || detail.result_state}`}>{INCIDENT_STATUS_LABELS[report?.incident_cause.status || ''] || STATE_LABELS[detail.result_state]}</span></header>
      <div className="cause-copy"><p className="cause-mechanism">{report?.incident_cause.mechanism || report?.summary || '调查正在按顺序收集事故版本代码与运行时证据。'}</p>
      {report?.incident_cause.why && <div className="cause-rationale"><span>判断依据</span><p>{report.incident_cause.why}</p></div>}</div>
      {report?.incident_cause.causal_chain?.length ? <ol className="causal-chain">{report.incident_cause.causal_chain.map((item, index) => <li key={`${item}-${index}`}><span>{index + 1}</span>{item}</li>)}</ol> : null}
    </section>

    <section className="code-diagnosis-band">
      <header><div><span>本项目代码诊断</span><h2>{report?.code_diagnosis.summary || '尚未形成代码诊断'}</h2></div>{report && <FindingState status={(report.code_diagnosis.status in CODE_STATUS_LABELS ? report.code_diagnosis.status : 'not_found') as InvestigationCodeFinding['status']} />}</header>
      {locatableFindings.length > 1 && <div className="finding-tabs">{locatableFindings.map((finding) => <button type="button" key={finding.id} aria-pressed={selectedFinding?.id === finding.id} onClick={() => setSelectedFindingId(finding.id)}>{finding.path}:{finding.start_line}</button>)}</div>}
      {selectedFinding ? <CodeFinding finding={selectedFinding} evidence={selectedEvidence} /> : diagnosisFinding ? <CodeDiagnosisOverview finding={diagnosisFinding} /> : <div className="diagnosis-empty"><FileCode2 size={18} /><p>{report?.code_diagnosis.status === 'no_defect' ? '本项目未发现与本次事故直接关联的代码缺陷。' : '没有通过服务端代码位置校验的最终可疑代码。'}</p></div>}
    </section>

    <section className="serial-execution-section">
      <div className="section-heading"><div><span>串行执行</span><h2>调查步骤与完整操作记录</h2></div><span>{detail.steps.length} 步 · {detail.operations.length} 个操作</span></div>
      <InvestigationWorkflow detail={detail} selectedOperationId={selectedOperationId} onSelectOperation={setSelectedOperationId} />
    </section>
    {auditOpen && <AuditDrawer investigationId={detail.id} onClose={() => setAuditOpen(false)} />}
    <ConfirmDialog open={archiveOpen} onOpenChange={setArchiveOpen} title="归档调查？" description="归档后该任务永久只读，不能再次重试或修改。" confirmLabel="归档" cancelLabel="取消" onConfirm={handleArchive} />
  </main>;
}

function FindingState({ status }: { status: InvestigationCodeFinding['status'] }) {
  const Icon = status === 'confirmed' || status === 'no_defect' ? CheckCircle2 : ShieldAlert;
  return <span className={`finding-state finding-state-${status}`}><Icon size={14} />{CODE_STATUS_LABELS[status]}</span>;
}

function CodeDiagnosisOverview({ finding }: { finding: InvestigationCodeFinding }) {
  const noDefect = finding.status === 'no_defect';
  return <div className="code-diagnosis-overview">
    <div className="diagnosis-verdict"><span className={`diagnosis-verdict-icon diagnosis-verdict-${finding.status}`}>{noDefect ? <CheckCircle2 size={18} /> : <ShieldAlert size={18} />}</span><div><h3>{noDefect ? '失败处理符合当前契约' : CODE_STATUS_LABELS[finding.status]}</h3><p>{noDefect ? '已定位到事故处理路径，当前行为符合已观察到的失败契约。' : '当前证据未形成可展示的精确错误代码范围。'}</p></div></div>
    <div className="diagnosis-facts-grid"><FindingFact label="代码行为" value={finding.faulty_behavior} /><FindingFact label="判断依据" value={finding.why_wrong} /><FindingFact label="事故触发" value={finding.trigger_condition} /><FindingFact label="传播路径" value={finding.causal_chain.join(' → ')} /><FindingFact label="预期契约" value={finding.expected_behavior} /><FindingFact label="验证方向" value={finding.fix_direction} /><FindingFact label="回归测试" value={finding.test_scenario} />{finding.missing_validation.length > 0 && <FindingFact label="尚缺验证" value={finding.missing_validation.join('；')} />}</div>
  </div>;
}

function CodeFinding({ finding, evidence }: { finding: InvestigationCodeFinding; evidence: InvestigationEvidence | null }) {
  const unverified = finding.status === 'hypothesis' && finding.revision_role === 'latest';
  return <div className="code-finding-layout">
    <div>{unverified && <div className="unverified-revision"><ShieldAlert size={14} />当前分支代码假设，未验证事故版本</div>}<InvestigationCodeViewer evidence={evidence} range={finding.start_line && finding.end_line ? { start: finding.start_line, end: finding.end_line } : null} /></div>
    <aside className="code-finding-explanation"><FindingFact label="哪里错" value={finding.faulty_behavior} /><FindingFact label="为什么错" value={finding.why_wrong} /><FindingFact label="触发条件" value={finding.trigger_condition} /><FindingFact label="如何传播" value={finding.causal_chain.join(' → ')} /><FindingFact label="预期行为" value={finding.expected_behavior} /><FindingFact label={finding.status === 'confirmed' ? '最小修复方向' : '验证方向'} value={finding.fix_direction} /><FindingFact label="验证测试" value={finding.test_scenario} />{finding.missing_validation.length > 0 && <FindingFact label="尚缺验证" value={finding.missing_validation.join('；')} />}</aside>
  </div>;
}

function FindingFact({ label, value }: { label: string; value: string }) {
  return value ? <section><span>{label}</span><p>{value}</p></section> : null;
}

function AuditDrawer({ investigationId, onClose }: { investigationId: string; onClose: () => void }) {
  const [operations, setOperations] = useState<Awaited<ReturnType<typeof fetchInvestigationAudit>>['operations']['items']>([]);
  const [aiCalls, setAiCalls] = useState<Awaited<ReturnType<typeof fetchInvestigationAudit>>['ai_calls']['items']>([]);
  const [operationNext, setOperationNext] = useState<number | null>(0);
  const [aiNext, setAiNext] = useState<number | null>(0);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => { if ((operationNext === null && aiNext === null) || loading) return; setLoading(true); try { const page = await fetchInvestigationAudit(investigationId, operationNext ?? Number.MAX_SAFE_INTEGER, aiNext ?? Number.MAX_SAFE_INTEGER); setOperations((value) => [...value, ...page.operations.items]); setAiCalls((value) => [...value, ...page.ai_calls.items]); setOperationNext(page.operations.next_cursor); setAiNext(page.ai_calls.next_cursor); } finally { setLoading(false); } }, [aiNext, investigationId, loading, operationNext]);
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="audit-backdrop" onMouseDown={onClose}><aside className="audit-drawer" role="dialog" aria-modal="true" aria-label="调查审计" onMouseDown={(event) => event.stopPropagation()}><header><div><span>调查审计</span><h2>完整操作与模型调用</h2></div><Button size="icon" variant="ghost" onClick={onClose} aria-label="关闭"><X size={16} /></Button></header><section className="audit-group"><h3>操作记录</h3>{operations.map((item) => <article key={item.id}><strong>{item.title}</strong><small>{item.actor} · {item.status} · {item.duration_ms ?? 0} ms</small><p>{item.purpose}</p><code>{JSON.stringify(item.input)}</code>{item.events.map((event) => <p key={event.sequence} className="audit-event-line">{event.message}</p>)}{item.failure && <p className="audit-failure">{item.failure.code}: {item.failure.detail}</p>}{item.evidence_refs.length > 0 && <small>归档证据 {item.evidence_refs.map((ref) => `#${ref}`).join(' · ')}</small>}</article>)}</section><section className="audit-group"><h3>模型调用</h3>{aiCalls.map((item) => <article key={item.id}><strong>{item.purpose}</strong><small>{item.model || '未配置模型'} · {item.status} · {item.latency_ms} ms · {item.total_tokens ?? 0} tokens</small><p>{item.error_detail || item.summary || item.error_code || '无摘要'}</p><code>{item.prompt_template_version} · {item.input_hash.slice(0, 12)}</code></article>)}</section>{(operationNext !== null || aiNext !== null) && <Button variant="outline" size="sm" disabled={loading} onClick={() => void load()}>{loading ? '加载中...' : '加载更多'}</Button>}</aside></div>;
}
