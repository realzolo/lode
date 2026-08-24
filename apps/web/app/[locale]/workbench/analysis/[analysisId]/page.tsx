'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/lib/navigation';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import type { AnalysisStatus, AnalysisStep } from '@/lib/types';
import {
  addGuidance,
  fetchAnalysis,
  reanalyze,
  submitAnalysisFeedback,
  toUiSteps,
  type AnalysisDetail,
} from '@/lib/api';
import { PIPELINE, WorkflowStepper } from '@/components/workflow-stepper';
import { IconRefreshCw, IconPlus, IconCopy, IconThumbsUp, IconThumbsDown } from '@/components/icons';

type NodeType = AnalysisStep['nodeType'];

const STEP_TITLE: Record<NodeType, string> = {
  receive: '接收告警', git_sync: '同步源码', context: '收集上下文',
  service_snapshot: '服务快照',
  ai_analysis: 'AI 根因分析', experience: '历史经验参考', conclusion: '生成结论',
};

const STEP_STATUS: Record<AnalysisStep['status'], string> = {
  done: '已完成', running: '运行中', pending: '等待中', degraded: '部分完成', failed: '失败', skipped: '已跳过',
};

const GUIDANCE_EFFECT = {
  will_apply: '将纳入本次分析', applied: '已纳入本次分析', needs_reanalysis: '需重新分析后生效',
} as const;

function statusVariant(status: AnalysisStatus): 'success' | 'warning' | 'danger' | 'accent' | 'default' {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'running' || status === 'needs_human' || status === 'needs_review') return 'warning';
  return 'accent';
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    : [];
}

function formatDuration(start?: string, finish?: string): string | null {
  if (!start || !finish) return null;
  const seconds = Math.max(0, Math.round((Date.parse(finish) - Date.parse(start)) / 1000));
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function DetailList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return <section className="analysis-detail-group"><h3>{title}</h3><ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>;
}

function EvidenceList({ detail, ids }: { detail: AnalysisDetail; ids?: number[] }) {
  const artifacts = detail.evidence_artifacts.filter((artifact) => !ids || ids.includes(artifact.id));
  if (!artifacts.length) return null;
  return <section className="analysis-detail-group"><h3>证据产物</h3><ul>{artifacts.map((artifact) => {
    const metadata = artifact.metadata ?? {};
    return <li key={artifact.id} id={`evidence-${artifact.id}`}><b>[{artifact.id}]</b> {artifact.artifact_type} · {artifact.locator ?? '未命名定位符'} · {String(metadata.time_scope ?? '时间范围未知')} · {artifact.collected_at}<br /><span className="mono">{artifact.redacted_excerpt ?? ''}</span></li>;
  })}</ul></section>;
}

function StepArtifacts({ node, detail }: { node: NodeType; detail: AnalysisDetail }) {
  const evidence = detail.evidence ?? {};
  if (node === 'receive' && detail.alert) {
    const fields = Object.entries(detail.alert.fields).map(([key, value]) => `${key}: ${JSON.stringify(value)}`);
    return <div className="analysis-key-values"><span>级别 <b>{detail.alert.level}</b></span><span>主题 <b>{detail.alert.topic}</b></span><span className="analysis-key-values-wide">错误 <b>{detail.alert.error_message || '未提供'}</b></span>{fields.map((field) => <span className="analysis-key-values-wide" key={field}>字段 <b className="mono">{field}</b></span>)}</div>;
  }
  if (node === 'git_sync') {
    const files = asRecords(evidence.git_evidence).map((item) => {
      const locator = typeof item.locator === 'string' ? item.locator : '未命名证据';
      const line = typeof item.line === 'number' ? `:${item.line}` : '';
      const terms = asStrings(item.terms).join(', ');
      return terms ? `${locator}${line}  ·  ${terms}` : `${locator}${line}`;
    });
    return <><DetailList title="检索模块" items={asStrings(evidence.modules)} /><DetailList title="源码证据" items={files} /></>;
  }
  if (node === 'context') return <DetailList title="允许查询的数据表" items={asStrings(evidence.allowed_tables)} />;
  if (node === 'service_snapshot') {
    const artifacts = detail.evidence_artifacts.filter((artifact) => artifact.artifact_type === 'service_snapshot');
    return <DetailList title="只读服务证据" items={artifacts.map((artifact) => {
      const metadata = artifact.metadata ?? {};
      return `${artifact.source_kind ?? 'service'} · ${String(metadata.summary ?? artifact.locator ?? '')} · 观测完成 ${String(metadata.observed_finished_at ?? artifact.collected_at)} · ${artifact.redacted_excerpt ?? ''}`;
    })} />;
  }
  if (node === 'ai_analysis') {
    const claims = (value: unknown) => asRecords(value).map((claim) => `${typeof claim.text === 'string' ? claim.text : ''} [证据 ${asStrings(claim.evidence_refs).join(', ') || (Array.isArray(claim.evidence_refs) ? (claim.evidence_refs as unknown[]).join(', ') : '无')}]`);
    return <><DetailList title="已确认事实" items={claims(evidence.facts)} /><DetailList title="推断" items={claims(evidence.inferences)} /><DetailList title="仍待确认" items={asStrings(evidence.unknowns)} /></>;
  }
  if (node === 'experience' && detail.matched_experience) return <section className="analysis-detail-group"><h3>历史经验参考</h3><p>{detail.matched_experience}</p><p className="muted">匹配方式：{String(evidence.matched_experience_type ?? 'unknown')}{typeof evidence.matched_experience_similarity === 'number' ? ` · 相似度 ${evidence.matched_experience_similarity.toFixed(2)}` : ''}</p></section>;
  if (node === 'conclusion') {
    const cited = asRecords(evidence.cited_evidence).map((item) => typeof item.id === 'number' ? item.id : null).filter((id): id is number => id !== null);
    return <EvidenceList detail={detail} ids={cited} />;
  }
  return null;
}

function RecommendationPanel({
  detail,
  canAnalyze,
  onFeedback,
}: {
  detail: AnalysisDetail;
  canAnalyze: boolean;
  onFeedback: (target: 'remediation' | 'agent_prompt', value: 'useful' | 'not_useful') => void;
}) {
  const t = useTranslations('analysis');
  const recommendation = detail.recommendation;
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  if (!recommendation) return null;
  const rec = recommendation;
  async function copyPrompt() {
    setCopyError(false);
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(rec.prompt_markdown);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = rec.prompt_markdown;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        const succeeded = document.execCommand('copy');
        textarea.remove();
        if (!succeeded) throw new Error('copy command was rejected');
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopyError(true);
    }
  }
  const feedback = detail.feedback;
  return <section className="analysis-recommendation" aria-labelledby="recommendation-heading">
    <div className="analysis-section-heading"><div><p className="analysis-eyebrow">{t('remediationEyebrow')}</p><h2 id="recommendation-heading">{t('remediationTitle')}</h2></div><span className={`analysis-risk analysis-risk-${rec.risk_level}`}>{rec.risk_level}</span></div>
    <p className="analysis-recommendation-summary">{rec.summary}</p>
    {rec.basis === 'safety_fallback' && <p className="analysis-review-notice">{t('safetyFallback')}</p>}
    {rec.preconditions.length > 0 && <DetailList title={t('preconditions')} items={rec.preconditions} />}
    {rec.steps.length > 0 && <section className="analysis-detail-group"><h3>{t('stepsLabel')}</h3><ol>{rec.steps.map((step, index) => <li key={`${step.action}-${index}`}><b>{step.action}</b><br /><span className="muted">{t('expectedResult')}：{step.expected_result}</span></li>)}</ol></section>}
    {rec.verification.length > 0 && <DetailList title={t('verification')} items={rec.verification} />}
    {rec.rollback.length > 0 && <DetailList title={t('rollback')} items={rec.rollback} />}
    <div className="analysis-agent-prompt"><div className="analysis-section-heading"><div><p className="analysis-eyebrow">{t('agentEyebrow')}</p><h3>{t('agentPrompt')}</h3></div><Button variant="default" size="sm" onClick={copyPrompt}><IconCopy size={15} /> {copied ? t('copied') : t('copyMarkdown')}</Button></div><pre>{rec.prompt_markdown}</pre><p className="muted">{copyError ? t('copyFailed') : t('redactedPrompt')}</p></div>
    {canAnalyze && <div className="analysis-feedback"><span className="muted">{t('feedbackQuestion')}</span><Button variant="default" size="sm" aria-pressed={feedback.my_remediation === 'useful'} onClick={() => onFeedback('remediation', 'useful')}><IconThumbsUp size={15} /> {t('remediationFeedback')} {feedback.remediation_useful || ''}</Button><Button variant="default" size="sm" aria-pressed={feedback.my_remediation === 'not_useful'} onClick={() => onFeedback('remediation', 'not_useful')}><IconThumbsDown size={15} /> {t('remediationFeedback')} {feedback.remediation_not_useful || ''}</Button><Button variant="default" size="sm" aria-pressed={feedback.my_agent_prompt === 'useful'} onClick={() => onFeedback('agent_prompt', 'useful')}><IconThumbsUp size={15} /> {t('promptFeedback')} {feedback.agent_prompt_useful || ''}</Button><Button variant="default" size="sm" aria-pressed={feedback.my_agent_prompt === 'not_useful'} onClick={() => onFeedback('agent_prompt', 'not_useful')}><IconThumbsDown size={15} /> {t('promptFeedback')} {feedback.agent_prompt_not_useful || ''}</Button></div>}
  </section>;
}

export default function AnalysisPage({ params }: { params: { analysisId: string } }) {
  const t = useTranslations('analysis');
  const tc = useTranslations('common');
  const router = useRouter();
  const analysisId = params.analysisId;
  const [detail, setDetail] = useState<AnalysisDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [guidance, setGuidance] = useState('');
  const [guidanceOpen, setGuidanceOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [selectedNode, setSelectedNode] = useState<NodeType>('receive');
  const selectedInitialized = useRef(false);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    if (pollRef.current) clearTimeout(pollRef.current);
    try {
      const data = await fetchAnalysis(analysisId);
      setDetail(data);
      setError(null);
      if (data.job.status === 'queued' || data.job.status === 'running' || data.job.status === 'retry_wait') {
        pollRef.current = setTimeout(() => void load(), 1500);
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setLoading(false);
    }
  }, [analysisId]);

  useEffect(() => {
    void load();
    return () => { if (pollRef.current) clearTimeout(pollRef.current); };
  }, [load]);

  useEffect(() => {
    if (!detail || selectedInitialized.current) return;
    const running = detail.steps.find((step) => step.status === 'running');
    const completed = detail.status === 'completed' ? 'conclusion' : detail.steps.filter((step) => step.status === 'completed').at(-1)?.node_type;
    setSelectedNode((running?.node_type ?? completed ?? 'receive') as NodeType);
    selectedInitialized.current = true;
  }, [detail]);

  async function handleReanalyze() {
    setBusy(true);
    try {
      const next = await reanalyze(analysisId);
      if (next.analysis_id === analysisId) {
        await load();
      } else {
        router.push(`/workbench/analysis/${next.analysis_id}`);
      }
    } finally { setBusy(false); }
  }

  async function handleAddGuidance() {
    if (!guidance.trim()) return;
    setBusy(true);
    try {
      await addGuidance(analysisId, guidance.trim());
      setGuidance('');
      setGuidanceOpen(false);
      await load();
    } finally { setBusy(false); }
  }

  async function handleFeedback(target: 'remediation' | 'agent_prompt', value: 'useful' | 'not_useful') {
    setFeedbackBusy(true);
    try {
      await submitAnalysisFeedback(analysisId, target, value);
      await load();
    } finally { setFeedbackBusy(false); }
  }

  if (loading && !detail) return <div className="analysis-loading" aria-busy="true"><Skeleton className="h-9 w-56" /><Skeleton className="h-4 w-80" /><Skeleton className="h-44 w-full" /><Skeleton className="h-72 w-full" /></div>;
  if (error) return <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>;
  if (!detail) return <p className="muted">{tc('empty')}</p>;

  const uiStatus = detail.status as AnalysisStatus;
  const steps = toUiSteps(detail.steps);
  const canAnalyze = detail.my_perm == null || detail.my_perm === 'analyze' || detail.my_perm === 'admin';
  const selectedStep = steps.find((step) => step.nodeType === selectedNode) ?? { nodeType: selectedNode, status: 'pending' as const };
  const totalDuration = formatDuration(detail.started_at ?? undefined, detail.finished_at ?? undefined);
  const lateGuidance = detail.guidances.some((item) => item.effect === 'needs_reanalysis');
  const jobStatus = detail.job.status;
  const queueMessage = jobStatus === 'queued'
    ? '告警已接收，正在等待分析 worker 领取任务。'
    : jobStatus === 'retry_wait'
      ? '分析将按重试策略重新执行。'
      : jobStatus === 'dead'
        ? `分析任务失败：${detail.job.last_error_detail ?? detail.job.last_error_code ?? '请查看任务错误。'}`
        : '分析正在汇集证据与上下文。';
  const jobLabel = jobStatus === 'queued'
    ? '等待 worker'
    : jobStatus === 'running'
      ? '执行中'
      : jobStatus === 'retry_wait'
        ? '等待重试'
        : jobStatus === 'dead'
          ? '任务失败'
          : '已完成';

  const evidence = detail.evidence ?? {};
  const warnings = asStrings(evidence.warnings);
  const coverage = typeof evidence.evidence_coverage === 'number' ? Math.round(evidence.evidence_coverage * 100) : null;
  return <main className="analysis-workspace">
    <header className="analysis-header">
      <div><p className="analysis-eyebrow">调查任务</p><h1 className="page-title">{t('title')}</h1><p className="mono muted">{analysisId}</p></div>
      <div className="analysis-actions"><Badge variant={statusVariant(uiStatus)}>{uiStatus}</Badge><Badge variant={jobStatus === 'dead' ? 'danger' : jobStatus === 'queued' || jobStatus === 'retry_wait' ? 'accent' : jobStatus === 'running' ? 'warning' : 'success'}>{jobLabel}</Badge>{totalDuration && <span className="analysis-duration">耗时 {totalDuration}</span>}{canAnalyze && <Button variant="primary" onClick={handleReanalyze} disabled={busy}><IconRefreshCw size={16} /> {tc('reanalyze')}</Button>}</div>
    </header>

    <section className="analysis-outcome" data-state={uiStatus} aria-live="polite"><div className="analysis-outcome-meta"><span>当前结论</span><span>{t('confidence')} {detail.confidence != null ? detail.confidence.toFixed(2) : '—'}{coverage != null ? ` · 证据覆盖 ${coverage}%` : ''}</span></div><p>{detail.conclusion ?? (uiStatus === 'failed' ? '分析未能完成，请查看失败步骤并补充分析引导。' : queueMessage)}</p>{uiStatus === 'needs_review' && <p className="analysis-review-notice">结果可供参考，但证据不完整，建议人工复核后再执行修复。</p>}{warnings.length > 0 && <DetailList title="分析警告" items={warnings} />}</section>

    <section className="analysis-flow"><div className="analysis-section-heading"><div><p className="analysis-eyebrow">调查轨迹</p><h2>{t('steps')}</h2></div><span className="muted">选择步骤查看调查细节</span></div><WorkflowStepper steps={steps} selected={selectedNode} onSelect={setSelectedNode} /></section>

    <section className="analysis-inspector" data-state={selectedStep.status}>
      <div className="analysis-inspector-head"><div><p className="analysis-eyebrow">阶段 {PIPELINE.findIndex((item) => item.nodeType === selectedNode) + 1} / {PIPELINE.length}</p><h2>{STEP_TITLE[selectedNode]}</h2></div><div className="analysis-inspector-status"><span>{STEP_STATUS[selectedStep.status]}</span>{formatDuration(selectedStep.startedAt, selectedStep.finishedAt) && <span>耗时 {formatDuration(selectedStep.startedAt, selectedStep.finishedAt)}</span>}</div></div>
      <p className="analysis-step-summary">{selectedStep.summary ?? (selectedStep.status === 'pending' ? '等待前置阶段完成。' : '该阶段暂未返回摘要。')}</p>
      {selectedStep.detail && <p className="analysis-step-detail">{selectedStep.detail}</p>}
      <StepArtifacts node={selectedNode} detail={detail} />
    </section>

    <RecommendationPanel detail={detail} canAnalyze={canAnalyze && !feedbackBusy} onFeedback={handleFeedback} />

    <section className="analysis-guidance" aria-labelledby="guidance-heading">
      <div className="analysis-section-heading"><div><p className="analysis-eyebrow">人工协作</p><h2 id="guidance-heading">分析引导</h2></div><Button variant="default" size="sm" onClick={() => setGuidanceOpen((open) => !open)} disabled={busy}><IconPlus size={15} /> 添加分析引导</Button></div>
      {guidanceOpen && <div className="analysis-guidance-composer"><Textarea value={guidance} onChange={(event) => setGuidance(event.target.value)} placeholder="补充与本次告警相关的事实、假设或排查方向..." autoFocus /><div><p className="muted">AI 推理开始前提交的引导会纳入本次分析；之后提交的内容会保留到下一次分析。</p><Button variant="primary" size="sm" onClick={handleAddGuidance} disabled={busy || !guidance.trim()}><IconPlus size={15} /> 提交引导</Button></div></div>}
      {detail.guidances.length ? <div className="analysis-guidance-list">{detail.guidances.map((item) => <article key={item.id} className="analysis-guidance-item" data-effect={item.effect}><div><span className="mono">{item.author}</span><span>{GUIDANCE_EFFECT[item.effect]}</span></div><p>{item.content}</p></article>)}</div> : <p className="muted">暂无分析引导。</p>}
      {lateGuidance && canAnalyze && <div className="analysis-follow-up"><span>{detail.follow_up_status === 'requested' ? '后续分析已登记，将在当前任务结束后开始。' : '存在尚未纳入当前推理的分析引导。'}</span>{detail.follow_up_status === 'none' && <Button variant="primary" size="sm" onClick={handleReanalyze} disabled={busy}><IconRefreshCw size={15} /> 使用引导重新分析</Button>}</div>}
    </section>
  </main>;
}
