'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, ChevronRight, CircleAlert, Clock3, Copy, FileSearch, Play, RefreshCw, Send, ShieldCheck, SlidersHorizontal, Workflow, X } from 'lucide-react';
import { InvestigationCodeViewer } from '@/components/investigation-code-viewer';
import { InvestigationGraph } from '@/components/investigation-graph';
import { InvestigationActivityLog, InvestigationWorkflow } from '@/components/investigation-workflow';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { INVESTIGATION_V2_CONTRACT_ERROR, fetchInvestigation, openInvestigationStream, reinvestigate, submitInvestigationFollowUp, type InvestigationDetail, type InvestigationLiveEvent, type InvestigationNodeStatus } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';

const SOURCE_LABELS: Record<string, string> = { alert: '告警', git: '源码', loki: 'Loki', prometheus: 'Prometheus', tempo: 'Tempo', postgres: 'PostgreSQL', redis: 'Redis', kafka: 'Kafka', clickhouse: 'ClickHouse', operator: '人工补充' };

function tone(status: string) { if (['succeeded', 'completed', 'confirmed', 'resolved', 'supported'].includes(status)) return 'ok'; if (['failed', 'violated', 'refuted'].includes(status)) return 'error'; if (['partial', 'blocked', 'provisional', 'insufficient', 'unavailable', 'open', 'required'].includes(status)) return 'warn'; return 'pending'; }
function maturityText(status: InvestigationDetail['result_state']) { return ({ confirmed: '当前结论', provisional: '调查进行中', insufficient: '证据不足', unavailable: '正在启动调查' } as Record<InvestigationDetail['result_state'], string>)[status]; }
function time(value: string | null | undefined) { return value ? new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '未提供'; }
function Status({ status, children }: { status: string; children?: React.ReactNode }) { return <span className={`investigation-status investigation-status-${tone(status)}`}><i />{children ?? status}</span>; }
function EvidenceRefs({ refs, onOpen }: { refs: number[]; onOpen: (id: number) => void }) { return refs.length ? <span className="investigation-refs">{refs.map((ref) => <button type="button" key={ref} onClick={() => onOpen(ref)}>证据 {ref}</button>)}</span> : null; }
function requirements(nodes: InvestigationDetail['nodes']) { return nodes.flatMap((node) => Array.isArray(node.outcome.evidence_requirements) ? node.outcome.evidence_requirements.filter((item): item is Record<string, string> => Boolean(item) && typeof item === 'object') : []); }

export default function InvestigationPage({ params }: { params: { investigationId: string } }) {
  const router = useRouter();
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [showFollowUp, setShowFollowUp] = useState(false);
  const [followUpText, setFollowUpText] = useState('');
  const [traceId, setTraceId] = useState('');
  const [deploymentSha, setDeploymentSha] = useState('');
  const cursor = useRef(0);
  const refresh = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnect = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [streamState, setStreamState] = useState<'connecting' | 'live' | 'reconnecting' | 'closed'>('connecting');
  const [liveEvents, setLiveEvents] = useState<InvestigationLiveEvent[]>([]);

  const load = useCallback(async () => {
    try {
      const data = await fetchInvestigation(params.investigationId);
      setDetail(data);
      cursor.current = Math.max(cursor.current, data.event_cursor);
      setLiveEvents((current) => current.length ? current : data.live_timeline);
      setError(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setLoading(false); }
  }, [params.investigationId]);
  useEffect(() => { void load(); }, [load]);
  const scheduleRefresh = useCallback(() => { if (refresh.current) clearTimeout(refresh.current); refresh.current = setTimeout(() => void load(), 160); }, [load]);
  const applyLiveEvent = useCallback((event: InvestigationLiveEvent) => {
    cursor.current = Math.max(cursor.current, event.sequence);
    setLiveEvents((current) => [...current.filter((item) => item.sequence !== event.sequence), event].sort((left, right) => left.sequence - right.sequence).slice(-160));
    setDetail((current) => {
      if (!current) return current;
      const terminal = ['succeeded', 'partial', 'blocked', 'failed', 'canceled'];
      const nodes = event.node_id ? current.nodes.map((node) => node.id === event.node_id ? { ...node, status: event.phase === 'started' || event.phase === 'progress' ? 'running' : (terminal.includes(event.phase) ? event.phase as InvestigationNodeStatus : node.status) } : node) : current.nodes;
      const currentActivity = { ...event, is_running: !terminal.includes(event.phase) && event.phase !== 'not_configured' };
      if (event.type === 'conclusion_updated') return { ...current, nodes, execution: { ...current.execution, current_activity: currentActivity }, conclusion: typeof event.detail.conclusion === 'string' ? event.detail.conclusion : current.conclusion, conclusion_version: typeof event.detail.conclusion_version === 'number' ? event.detail.conclusion_version : current.conclusion_version };
      if (event.type === 'terminal') return { ...current, nodes, execution: { ...current.execution, current_activity: currentActivity }, status: event.detail.status === 'failed' ? 'failed' : 'completed' };
      return { ...current, nodes, execution: { ...current.execution, current_activity: currentActivity } };
    });
    if (['plan_changed', 'reasoning_updated', 'conclusion_updated', 'terminal'].includes(event.type)) scheduleRefresh();
  }, [scheduleRefresh]);
  useEffect(() => {
    let disposed = false;
    let close = () => {};
    const connect = () => {
      if (disposed) return;
      setStreamState(cursor.current ? 'reconnecting' : 'connecting');
      close = openInvestigationStream(params.investigationId, cursor.current, {
        onSnapshot: (snapshot) => { cursor.current = Math.max(cursor.current, snapshot.sequence); setStreamState(snapshot.status === 'completed' || snapshot.status === 'failed' ? 'closed' : 'live'); },
        onEvent: (event) => { setStreamState('live'); applyLiveEvent(event); },
        onClose: () => { if (!disposed) setStreamState('closed'); },
        onError: () => { if (!disposed) { setStreamState('reconnecting'); reconnect.current = setTimeout(connect, 1500); } },
      });
    };
    connect();
    return () => { disposed = true; close(); if (refresh.current) clearTimeout(refresh.current); if (reconnect.current) clearTimeout(reconnect.current); };
  }, [applyLiveEvent, params.investigationId]);
  useEffect(() => {
    if (!detail) return;
    const preferred = detail.nodes.find((node) => node.status === 'running') || detail.nodes.find((node) => ['partial', 'blocked', 'failed'].includes(node.status)) || detail.nodes[0];
    setSelectedNodeId((current) => current || preferred?.id || null);
    setSelectedOperationId((current) => current || preferred?.operations.find((operation) => operation.status === 'running')?.id || preferred?.operations[preferred.operations.length - 1]?.id || null);
  }, [detail]);

  const selectedNode = detail?.nodes.find((node) => node.id === selectedNodeId) || detail?.nodes[0] || null;
  const directCause = detail?.brief?.direct_cause || null;
  const directCauseRefs = useMemo(() => new Set(directCause?.status === 'confirmed' ? directCause.evidence_refs : []), [directCause]);
  const hasDirectCause = directCause?.status === 'confirmed' && directCauseRefs.size > 0;
  const filteredEvidence = useMemo(() => (detail?.evidence || []).filter((item) => {
    const content = `${item.locator || ''} ${item.excerpt} ${item.type} ${item.source}`.toLowerCase();
    return directCauseRefs.has(item.id) && (!search || content.includes(search.toLowerCase())) && (sourceFilter === 'all' || item.source === sourceFilter) && (typeFilter === 'all' || item.type === typeFilter);
  }), [detail?.evidence, directCauseRefs, search, sourceFilter, typeFilter]);
  const activeEvidence = detail?.evidence.find((item) => item.id === selectedEvidenceId) || filteredEvidence[0] || null;
  const evidenceRequirements = useMemo(() => detail ? requirements(detail.nodes) : [], [detail]);
  const selectExecution = (selection: { nodeId: string; operationId?: string }) => { setSelectedNodeId(selection.nodeId); setSelectedOperationId(selection.operationId || null); };
  const openEvidence = (id: number) => { setSelectedEvidenceId(id); requestAnimationFrame(() => document.getElementById('evidence-explorer')?.scrollIntoView({ behavior: 'smooth', block: 'center' })); };
  async function restart() { setBusy(true); try { const next = await reinvestigate(detail?.id || params.investigationId); router.push(`/workbench/investigation/${next.id}`); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); setBusy(false); } }
  async function submitFollowUp() { if (!detail || (!followUpText.trim() && !traceId.trim() && !deploymentSha.trim())) return; setBusy(true); try { const next = await submitInvestigationFollowUp(detail.id, { evidence: followUpText.trim() ? [{ kind: 'log', content: followUpText.trim() }] : [], scope_patch: { trace_id: traceId.trim() || undefined, deployment_sha: deploymentSha.trim() || undefined } }); router.push(`/workbench/investigation/${next.id}`); } catch (cause) { setError(String(cause)); setBusy(false); } }

  if (loading) return <div className="investigation-loading"><Workflow size={20} />正在加载调查工作台...</div>;
  if (error || !detail) return <div className="investigation-loading investigation-error"><CircleAlert size={20} />{error || '调查不存在'}{error === INVESTIGATION_V2_CONTRACT_ERROR ? <Button size="sm" variant="primary" onClick={() => void restart()} disabled={busy}><Play size={14} />重新调查</Button> : <Button size="sm" variant="outline" onClick={() => void load()}>重试</Button>}</div>;
  const scope = [detail.application.name, detail.scope.service || '服务未标注', detail.scope.environment || '环境未标注'].join(' · ');
  const activity = detail.execution.current_activity;
  return <main className="investigation-page investigation-workbench investigation-single-page">
    <header className="investigation-header workbench-header">
      <div className="investigation-breadcrumb"><Link href="/workbench">调查</Link><ChevronRight size={14} /><span>{detail.application.name}</span><ChevronRight size={14} /><span className="mono">{detail.id.slice(0, 12)}</span></div>
      <div className="investigation-header-row"><div className="investigation-title"><div className={`investigation-severity investigation-severity-${detail.alert?.level === 'CRITICAL' ? 'critical' : 'warning'}`}><CircleAlert size={18} /></div><div><h1>{detail.alert?.title || '未命名事故'}</h1><p>{scope}</p></div></div><div className="investigation-actions"><Button size="icon" variant="outline" onClick={() => void load()} aria-label="刷新调查"><RefreshCw size={15} /></Button><Button size="sm" variant="outline" onClick={() => setAuditOpen(true)}><SlidersHorizontal size={14} />审计</Button><Button size="sm" variant="primary" onClick={() => void restart()} disabled={busy}><Play size={14} />重新调查</Button></div></div>
      <div className="investigation-meta"><Status status={detail.status === 'running' ? 'running' : detail.result_state}>{maturityText(detail.result_state)}</Status><span className={`stream-status stream-status-${streamState}`}>{streamState === 'live' ? '实时更新' : streamState === 'reconnecting' ? '正在重连' : streamState === 'closed' ? '调查已归档' : '正在连接'}</span>{detail.review_required && <Status status="blocked">生产处置待审批</Status>}<span className="investigation-meta-item"><Clock3 size={14} />{time(detail.scope.window_started_at)} - {time(detail.scope.window_finished_at)}</span></div>
    </header>

    <section className="ai-command-center" aria-label="AI 调查指挥">
      <div className="ai-command-marker"><Bot size={19} /><span>Lode Agent</span></div>
      <div className="ai-command-main"><span>{activity?.display.actor === 'ai' ? 'AI 正在调查' : '调查引擎正在执行'}</span><h2>{activity?.display.headline || '正在准备调查路径'}</h2><p>{activity?.display.message || '正在确认已授权能力与可用证据范围。'}</p><div className="ai-command-meta"><span><b>依据</b>{selectedNode?.selection_reason || '已归档告警与已绑定的只读能力'}</span><span><b>目标</b>{selectedNode?.expected_evidence || '生成最小可执行调查路径'}</span></div></div>
      {activity && activity.display.evidence_refs.length > 0 && <button type="button" className="ai-command-evidence" onClick={() => openEvidence(activity.display.evidence_refs[0])}><FileSearch size={15} />查看 {activity.display.evidence_refs.length} 项相关证据</button>}
    </section>

    <section className="single-page-section assessment-section" aria-label="当前研判">
      <div className="section-heading"><div><span>当前研判</span><h2>{detail.brief?.headline || '正在根据已授权证据形成可引用的研判'}</h2></div><Status status={detail.result_state}>{maturityText(detail.result_state)}</Status></div>
      {detail.brief ? <><p className="assessment-summary">{detail.brief.summary}</p><div className={`direct-cause-callout direct-cause-${detail.brief.direct_cause.status}`}><span>AI 对直接原因的判断</span><strong>{detail.brief.direct_cause.text}</strong><EvidenceRefs refs={detail.brief.direct_cause.evidence_refs} onOpen={openEvidence} /></div><div className="assessment-columns"><BriefGroup title="已证实" items={detail.brief.confirmed} onOpen={openEvidence} /><BriefGroup title="影响" items={detail.brief.impact} onOpen={openEvidence} /><BriefGroup title="尚未证实 / 反证缺口" items={detail.brief.uncertain} onOpen={openEvidence} /></div><div className="assessment-next"><span>建议下一步</span><strong>{detail.brief.next_step.text}</strong><EvidenceRefs refs={detail.brief.next_step.evidence_refs} onOpen={openEvidence} /></div>{detail.remediation && <div className="assessment-remediation"><span>建议处置</span><strong>{detail.remediation.summary}</strong><EvidenceRefs refs={detail.remediation.evidence_refs} onOpen={openEvidence} /></div>}</> : <div className="assessment-pending"><Workflow size={18} /><div><strong>等待首轮证据收集完成</strong><p>调查不会展示内部推理；证据归因完成后会在这里给出已证实、未证实和下一步。</p></div></div>}
    </section>

    <section className="single-page-section execution-section" aria-label="执行进度">
      <div className="section-heading"><div><span>执行进度</span><h2>调查任务与实时操作流</h2></div><span>{detail.execution.operation_count} 个可审计操作</span></div>
      <div className="execution-live-grid"><InvestigationWorkflow detail={detail} selectedNodeId={selectedNodeId} selectedOperationId={selectedOperationId} onSelect={selectExecution} /><InvestigationActivityLog events={liveEvents} selectedNodeId={selectedNodeId} selectedOperationId={selectedOperationId} onSelect={selectExecution} onOpenEvidence={openEvidence} /></div>
    </section>

    <section className="single-page-section evidence-section" id="evidence-explorer" aria-label="证据探索">
      <div className="section-heading"><div><span>直接原因证据</span><h2>{hasDirectCause ? '证明本次错误直接原因的最小证据' : 'AI 尚未确认直接原因'}</h2></div><span>{hasDirectCause ? `${filteredEvidence.length}/${directCauseRefs.size} 项` : '等待交叉验证'}</span></div>
      {hasDirectCause ? <div className="evidence-workspace single-evidence-workspace"><aside className="evidence-rail"><div className="evidence-filter"><FileSearch size={16} /><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索直接原因证据" aria-label="搜索直接原因证据" /></div><div className="evidence-filter-grid"><Select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)} aria-label="证据来源"><option value="all">全部来源</option>{[...new Set(detail.evidence.filter((item) => directCauseRefs.has(item.id)).map((item) => item.source))].map((source) => <option value={source} key={source}>{SOURCE_LABELS[source] || source}</option>)}</Select><Select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)} aria-label="证据类型"><option value="all">全部类型</option>{[...new Set(detail.evidence.filter((item) => directCauseRefs.has(item.id)).map((item) => item.type))].map((type) => <option value={type} key={type}>{type}</option>)}</Select></div><div className="evidence-list" role="list">{filteredEvidence.map((item) => <button type="button" role="listitem" key={item.id} className={activeEvidence?.id === item.id ? 'is-selected' : ''} onClick={() => setSelectedEvidenceId(item.id)}><span>{SOURCE_LABELS[item.source] || item.source} · {item.type}</span><strong>{item.code?.mode === 'source' ? item.code.anchor.path : item.locator || '无定位信息'}</strong><p>{item.excerpt}</p><small>{time(item.collected_at)} · 直接原因引用</small></button>)}{!filteredEvidence.length && <p className="muted">没有符合当前筛选的直接原因证据。</p>}</div></aside><EvidenceInspector evidence={activeEvidence} onOpen={openEvidence} /></div> : <DirectCauseEvidenceState directCause={directCause} activity={activity} />}
    </section>

    <section className="single-page-section reasoning-remediation-grid" aria-label="证据路径与处置建议">
      <section className="reasoning-panel"><div className="section-heading"><div><span>证据路径</span><h2>从事实到当前判断</h2></div><span>{detail.reasoning_edges.length} 条关系</span></div><InvestigationGraph detail={detail} selectedId={null} onOpenEvidence={openEvidence} /></section>
      <section className="remediation-workspace single-remediation"><div className="section-heading"><div><span>风险受控处置</span><h2>{detail.remediation?.summary || '处置建议将在归因完成后生成。'}</h2></div>{detail.remediation && <Status status={detail.remediation.risk_level === 'low' ? 'succeeded' : 'blocked'}>{detail.remediation.risk_level} 风险</Status>}</div>{detail.remediation && <><EvidenceRefs refs={detail.remediation.evidence_refs} onOpen={openEvidence} /><div className="remediation-columns"><DetailList title="前置条件" items={detail.remediation.preconditions} /><DetailList title="建议步骤" items={detail.remediation.steps.map((step) => step.action || '建议步骤')} /><DetailList title="验证" items={detail.remediation.verification} /><DetailList title="回滚边界" items={detail.remediation.rollback} /></div><div className="approval-context"><span>人工审批上下文</span><Button size="icon" variant="outline" aria-label="复制人工审批上下文" onClick={() => void navigator.clipboard.writeText(detail.remediation?.agent_prompt || '')}><Copy size={14} /></Button></div></>}{evidenceRequirements.length > 0 && <section className="follow-up-panel"><div><span>补充最小证据</span><h3>提交后创建继承调查并重新收敛</h3><ul>{evidenceRequirements.map((item, index) => <li key={index}>{item.field || '缺失证据'}：{item.minimum || item.why}</li>)}</ul></div><Button size="sm" variant="outline" onClick={() => setShowFollowUp((value) => !value)}><Send size={14} />补充证据</Button>{showFollowUp && <div className="follow-up-form"><textarea value={followUpText} onChange={(event) => setFollowUpText(event.target.value)} placeholder="粘贴脱敏日志、网关响应或依赖事实" /><input value={traceId} onChange={(event) => setTraceId(event.target.value)} placeholder="Trace ID（可选）" /><input value={deploymentSha} onChange={(event) => setDeploymentSha(event.target.value)} placeholder="部署版本 / commit SHA（可选）" /><Button size="sm" variant="primary" onClick={() => void submitFollowUp()} disabled={busy || (!followUpText.trim() && !traceId.trim() && !deploymentSha.trim())}><Send size={14} />创建继承调查</Button></div>}</section>}</section>
    </section>
    {auditOpen && <AuditDrawer detail={detail} events={liveEvents} selectedNode={selectedNode} onClose={() => setAuditOpen(false)} onOpenEvidence={openEvidence} />}
  </main>;
}

function BriefGroup({ title, items, onOpen }: { title: string; items: NonNullable<InvestigationDetail['brief']>['confirmed']; onOpen: (id: number) => void }) { return <section><span>{title}</span>{items.length ? items.map((item, index) => <article key={`${item.text}-${index}`}><p>{item.text}</p><EvidenceRefs refs={item.evidence_refs} onOpen={onOpen} /></article>) : <p className="muted">暂无可展示项</p>}</section>; }
function DetailList({ title, items }: { title: string; items: string[] }) { return <section><h3>{title}</h3>{items.length ? <ol>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ol> : <p className="muted">未提供</p>}</section>; }
function DirectCauseEvidenceState({ directCause, activity }: { directCause: NonNullable<InvestigationDetail['brief']>['direct_cause'] | null; activity: InvestigationDetail['execution']['current_activity'] }) { return <section className="evidence-empty-state"><FileSearch size={20} /><div><strong>{directCause?.status === 'not_proven' ? '当前不能证明直接原因' : 'AI 正在建立直接原因证据链'}</strong><p>{directCause?.text || activity?.display.message || 'AI 需要将事故信号与事故版本代码或运行时响应交叉验证；候选文件不会被当作直接原因展示。'}</p></div></section>; }
function EvidenceInspector({ evidence, onOpen }: { evidence: InvestigationDetail['evidence'][number] | null; onOpen: (id: number) => void }) { if (!evidence) return <section className="evidence-inspector evidence-inspector-empty"><FileSearch size={20} /><p>选择一项证据以检查其来源、版本和上下文。</p></section>; const source = evidence.code?.mode === 'source' ? evidence.code.anchor : null; return <section className="evidence-inspector"><header><div><span>{SOURCE_LABELS[evidence.source] || evidence.source} · {evidence.type}</span><h2>{source?.path || evidence.locator || '已脱敏证据'}</h2></div><Status status="succeeded">不可变</Status></header><div className="evidence-inspector-meta">{source && <><span>{source.revision.slice(0, 12)}</span><span>第 {source.match_line} 行</span><span>范围 {source.snippet_start_line}-{source.snippet_end_line}</span></>}<span>{time(evidence.collected_at)}</span></div>{evidence.code ? <InvestigationCodeViewer evidence={evidence} /> : <pre className="evidence-text-preview">{evidence.excerpt}</pre>}<div className="evidence-inspector-foot"><ShieldCheck size={15} />内容已脱敏并以内容哈希固化</div><EvidenceRefs refs={[evidence.id]} onOpen={onOpen} /></section>; }
function AuditDrawer({ detail, events, selectedNode, onClose, onOpenEvidence }: { detail: InvestigationDetail; events: InvestigationLiveEvent[]; selectedNode: InvestigationDetail['nodes'][number] | null; onClose: () => void; onOpenEvidence: (id: number) => void }) { return <div className="audit-backdrop" role="presentation" onMouseDown={onClose}><aside className="audit-drawer" role="dialog" aria-modal="true" aria-label="调查审计" onMouseDown={(event) => event.stopPropagation()}><header><div><span>调查审计</span><h2>可追溯执行记录</h2></div><Button size="icon" variant="ghost" onClick={onClose} aria-label="关闭审计"><X size={16} /></Button></header><section><span>模型参与</span><p>{detail.ai_usage.call_count ? `已记录 ${detail.ai_usage.call_count} 次模型调用。` : '本次调查未使用可用模型响应。'}</p>{detail.ai_usage.calls.map((call, index) => <article key={index}><strong>{call.purpose}</strong><small>{call.model || '未配置模型'} · {call.status} · {call.latency_ms} ms</small><p>{call.summary || call.error_code || '未产生展示摘要'}</p><EvidenceRefs refs={call.evidence_refs} onOpen={onOpenEvidence} /></article>)}</section>{selectedNode && <section><span>当前节点边界</span><p>{selectedNode.decision_rule}</p><small>预算：{Object.entries(selectedNode.budget).map(([key, value]) => `${key} ${String(value)}`).join(' · ')}</small></section>}<section><span>原始事件</span>{events.slice(-8).reverse().map((event) => <article key={event.sequence}><strong>{event.type.replaceAll('_', ' ')}</strong><small>{time(event.occurred_at)} · {event.phase}</small><pre>{JSON.stringify(event.detail, null, 2)}</pre><EvidenceRefs refs={event.artifact_refs} onOpen={onOpenEvidence} /></article>)}</section></aside></div>; }
