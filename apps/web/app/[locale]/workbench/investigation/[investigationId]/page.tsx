'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronRight, Clock3, FileCode2, FileSearch, RefreshCw, ShieldAlert, SlidersHorizontal, X } from 'lucide-react';
import { InvestigationCodeViewer } from '@/components/investigation-code-viewer';
import { InvestigationWorkflow } from '@/components/investigation-workflow';
import { Button } from '@/components/ui/button';
import { fetchInvestigation, fetchInvestigationAudit, openInvestigationStream, type InvestigationCodeFinding, type InvestigationDetail, type InvestigationEvidence } from '@/lib/api';
import { Link } from '@/lib/navigation';

const STATE_LABELS = { pending: '调查中', confirmed: '已确认', hypothesis: '待验证假设', insufficient: '证据不足', unavailable: '分析不可用' } as const;

function formatTime(value: string | null | undefined) {
  return value ? new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)) : '未提供';
}

function EvidenceLinks({ refs, onOpen }: { refs: number[]; onOpen: (id: number) => void }) {
  return refs.length ? <div className="serial-evidence-links">{refs.map((ref) => <button key={ref} type="button" onClick={() => onOpen(ref)}><FileSearch size={12} />证据 {ref}</button>)}</div> : null;
}

export default function InvestigationPage({ params }: { params: { investigationId: string } }) {
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<number | null>(null);
  const [selectedFindingId, setSelectedFindingId] = useState<number | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cursor = useRef(0);
  const load = useCallback(async () => {
    try {
      const data = await fetchInvestigation(params.investigationId);
      setDetail(data);
      cursor.current = Math.max(cursor.current, data.event_cursor);
      setSelectedOperationId((value) => value || data.operations.find((item) => item.status === 'running')?.id || data.operations.at(-1)?.id || null);
      setSelectedFindingId((value) => value || data.code_findings[0]?.id || null);
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

  const selectedFinding = detail?.code_findings.find((item) => item.id === selectedFindingId) || detail?.code_findings[0] || null;
  const selectedEvidence = detail?.evidence.find((item) => item.id === selectedEvidenceId)
    || detail?.evidence.find((item) => item.id === selectedFinding?.artifact_id)
    || null;
  const openEvidence = (id: number) => {
    setSelectedEvidenceId(id);
    requestAnimationFrame(() => document.getElementById('evidence')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  };
  if (loading) return <div className="investigation-loading">正在加载调查...</div>;
  if (error || !detail) return <div className="investigation-loading investigation-error">{error || '调查不存在'}<Button variant="outline" size="sm" onClick={() => void load()}>重试</Button></div>;

  const currentStep = detail.steps.find((item) => item.status === 'running');
  const report = detail.report;
  return <main className="investigation-page investigation-workbench serial-investigation-page">
    <header className="investigation-header serial-header">
      <div className="investigation-breadcrumb"><Link href="/workbench">调查</Link><ChevronRight size={14} /><span>{detail.application_name}</span><ChevronRight size={14} /><span className="mono">{detail.id.slice(0, 12)}</span></div>
      <div className="investigation-header-row"><div className="investigation-title"><div className={`investigation-severity investigation-severity-${detail.input?.severity === 'CRITICAL' ? 'critical' : 'warning'}`}><AlertTriangle size={18} /></div><div><h1>{detail.input?.title || '事故调查'}</h1><p>{detail.input?.error.name}: {detail.input?.error.message}</p></div></div><div className="investigation-actions"><Button size="icon" variant="outline" onClick={() => void load()} aria-label="刷新"><RefreshCw size={15} /></Button><Button size="sm" variant="outline" onClick={() => setAuditOpen(true)}><SlidersHorizontal size={14} />审计</Button></div></div>
      <div className="investigation-meta"><span className={`result-state result-state-${detail.result_state}`}>{STATE_LABELS[detail.result_state]}</span><span><Clock3 size={13} />{formatTime(detail.input?.occurred_at)}</span><span>{detail.scope.deployment_sha ? `部署 ${detail.scope.deployment_sha.slice(0, 12)}` : '未提供事故部署版本'}</span>{currentStep && <span>当前步骤：{currentStep.title}</span>}</div>
    </header>

    <section className="cause-band">
      <header><div><span>事故真实原因</span><h2>{report?.headline || '等待证据归因'}</h2></div><span className={`result-state result-state-${detail.result_state}`}>{STATE_LABELS[detail.result_state]}</span></header>
      <p className="cause-mechanism">{report?.incident_cause.mechanism || report?.summary || '调查正在按顺序收集事故版本代码与运行时证据。'}</p>
      {report?.incident_cause.why && <p>{report.incident_cause.why}</p>}
      {report?.incident_cause.causal_chain?.length ? <ol className="causal-chain">{report.incident_cause.causal_chain.map((item, index) => <li key={`${item}-${index}`}><span>{index + 1}</span>{item}</li>)}</ol> : null}
      <EvidenceLinks refs={report?.incident_cause.evidence_refs || []} onOpen={openEvidence} />
    </section>

    <section className="code-diagnosis-band">
      <header><div><span>本项目代码诊断</span><h2>{report?.code_diagnosis.summary || '尚未形成代码诊断'}</h2></div>{selectedFinding && <span className={`finding-state finding-state-${selectedFinding.status}`}>{selectedFinding.status === 'confirmed' ? <CheckCircle2 size={14} /> : <ShieldAlert size={14} />}{selectedFinding.status}</span>}</header>
      {detail.code_findings.length > 1 && <div className="finding-tabs">{detail.code_findings.map((finding) => <button type="button" key={finding.id} aria-pressed={selectedFinding?.id === finding.id} onClick={() => setSelectedFindingId(finding.id)}>{finding.path ? `${finding.path}:${finding.start_line}` : finding.status}</button>)}</div>}
      {selectedFinding ? <CodeFinding finding={selectedFinding} evidence={selectedEvidence} onOpenEvidence={openEvidence} /> : <div className="diagnosis-empty"><FileCode2 size={18} /><p>{report?.code_diagnosis.status === 'no_defect' ? '本项目未发现与本次事故直接关联的代码缺陷。' : '没有通过服务端代码位置校验的 finding。'}</p></div>}
    </section>

    <section className="serial-execution-section">
      <div className="section-heading"><div><span>串行执行</span><h2>调查步骤与完整操作记录</h2></div><span>{detail.steps.length} 步 · {detail.operations.length} 个操作</span></div>
      <InvestigationWorkflow detail={detail} selectedOperationId={selectedOperationId} onSelectOperation={setSelectedOperationId} onOpenEvidence={openEvidence} />
    </section>

    <section className="evidence-library" id="evidence">
      <div className="section-heading"><div><span>调查证据</span><h2>代码候选、运行时事实与反证</h2></div><span>{detail.evidence.length} 项</span></div>
      <div className="evidence-layout"><aside>{detail.evidence.map((evidence) => <button type="button" key={evidence.id} className={selectedEvidence?.id === evidence.id ? 'is-selected' : ''} onClick={() => setSelectedEvidenceId(evidence.id)}><span>{evidence.source} · {evidence.type}</span><strong>{evidence.code?.anchor.path || evidence.locator || `证据 ${evidence.id}`}</strong><small>{String(evidence.metadata.selection_basis || '归档事实')}</small></button>)}</aside><EvidenceDetail evidence={selectedEvidence} /></div>
    </section>
    {auditOpen && <AuditDrawer investigationId={detail.id} onClose={() => setAuditOpen(false)} onOpenEvidence={openEvidence} />}
  </main>;
}

function CodeFinding({ finding, evidence, onOpenEvidence }: { finding: InvestigationCodeFinding; evidence: InvestigationEvidence | null; onOpenEvidence: (id: number) => void }) {
  const unverified = finding.status === 'hypothesis' && finding.revision_role === 'latest';
  return <div className="code-finding-layout">
    <div>{unverified && <div className="unverified-revision"><ShieldAlert size={14} />当前分支代码假设，未验证事故版本</div>}<InvestigationCodeViewer evidence={evidence} range={finding.start_line && finding.end_line ? { start: finding.start_line, end: finding.end_line } : null} /></div>
    <aside className="code-finding-explanation"><FindingFact label="哪里错" value={finding.faulty_behavior} /><FindingFact label="为什么错" value={finding.why_wrong} /><FindingFact label="触发条件" value={finding.trigger_condition} /><FindingFact label="如何传播" value={finding.causal_chain.join(' → ')} /><FindingFact label="预期行为" value={finding.expected_behavior} /><FindingFact label={finding.status === 'confirmed' ? '最小修复方向' : '验证方向'} value={finding.fix_direction} /><FindingFact label="验证测试" value={finding.test_scenario} />{finding.missing_validation.length > 0 && <FindingFact label="尚缺验证" value={finding.missing_validation.join('；')} />}<EvidenceLinks refs={[...finding.incident_evidence_refs, ...finding.supporting_evidence_refs]} onOpen={onOpenEvidence} /></aside>
  </div>;
}

function FindingFact({ label, value }: { label: string; value: string }) {
  return value ? <section><span>{label}</span><p>{value}</p></section> : null;
}

function EvidenceDetail({ evidence }: { evidence: InvestigationEvidence | null }) {
  if (!evidence) return <div className="evidence-detail-empty">选择证据查看不可变内容。</div>;
  return <article className="evidence-detail"><header><div><span>{evidence.source} · {evidence.type}</span><h3>{evidence.code?.anchor.path || evidence.locator || `证据 ${evidence.id}`}</h3></div><code>{evidence.content_hash.slice(0, 12)}</code></header>{evidence.code ? <InvestigationCodeViewer evidence={evidence} /> : <pre>{evidence.excerpt}</pre>}<dl><div><dt>采集时间</dt><dd>{formatTime(evidence.collected_at)}</dd></div><div><dt>定位</dt><dd>{evidence.locator || '无外部定位'}</dd></div></dl></article>;
}

function AuditDrawer({ investigationId, onClose, onOpenEvidence }: { investigationId: string; onClose: () => void; onOpenEvidence: (id: number) => void }) {
  const [operations, setOperations] = useState<Awaited<ReturnType<typeof fetchInvestigationAudit>>['operations']['items']>([]);
  const [aiCalls, setAiCalls] = useState<Awaited<ReturnType<typeof fetchInvestigationAudit>>['ai_calls']['items']>([]);
  const [operationNext, setOperationNext] = useState<number | null>(0);
  const [aiNext, setAiNext] = useState<number | null>(0);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => { if ((operationNext === null && aiNext === null) || loading) return; setLoading(true); try { const page = await fetchInvestigationAudit(investigationId, operationNext ?? Number.MAX_SAFE_INTEGER, aiNext ?? Number.MAX_SAFE_INTEGER); setOperations((value) => [...value, ...page.operations.items]); setAiCalls((value) => [...value, ...page.ai_calls.items]); setOperationNext(page.operations.next_cursor); setAiNext(page.ai_calls.next_cursor); } finally { setLoading(false); } }, [aiNext, investigationId, loading, operationNext]);
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="audit-backdrop" onMouseDown={onClose}><aside className="audit-drawer" role="dialog" aria-modal="true" aria-label="调查审计" onMouseDown={(event) => event.stopPropagation()}><header><div><span>调查审计</span><h2>完整操作与模型调用</h2></div><Button size="icon" variant="ghost" onClick={onClose} aria-label="关闭"><X size={16} /></Button></header><section className="audit-group"><h3>操作记录</h3>{operations.map((item) => <article key={item.id}><strong>{item.title}</strong><small>{item.actor} · {item.status} · {item.duration_ms ?? 0} ms</small><p>{item.purpose}</p><code>{JSON.stringify(item.input)}</code>{item.events.map((event) => <p key={event.sequence} className="audit-event-line">{event.message}</p>)}{item.failure && <p className="audit-failure">{item.failure.code}: {item.failure.detail}</p>}<EvidenceLinks refs={item.evidence_refs} onOpen={onOpenEvidence} /></article>)}</section><section className="audit-group"><h3>模型调用</h3>{aiCalls.map((item) => <article key={item.id}><strong>{item.purpose}</strong><small>{item.model || '未配置模型'} · {item.status} · {item.latency_ms} ms · {item.total_tokens ?? 0} tokens</small><p>{item.summary || item.error_code || '无摘要'}</p><code>{item.prompt_template_version} · {item.input_hash.slice(0, 12)}</code><EvidenceLinks refs={item.evidence_refs} onOpen={onOpenEvidence} /></article>)}</section>{(operationNext !== null || aiNext !== null) && <Button variant="outline" size="sm" disabled={loading} onClick={() => void load()}>{loading ? '加载中...' : '加载更多'}</Button>}</aside></div>;
}
