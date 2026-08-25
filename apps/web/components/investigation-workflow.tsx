'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, Bot, ChevronDown, Radio, RotateCcw } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import type { InvestigationDetail, InvestigationLiveEvent, InvestigationNodeStatus } from '@/lib/api';

type Selection = { nodeId: string; operationId?: string };

function nodeTone(status: InvestigationNodeStatus) {
  if (status === 'succeeded') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'partial' || status === 'blocked') return 'warning';
  if (status === 'running') return 'active';
  return 'pending';
}

function statusText(status: string) {
  return ({ queued: '等待中', running: '执行中', succeeded: '完成', partial: '已收敛', blocked: '等待证据', failed: '失败', canceled: '已取消', not_configured: '未配置' } as Record<string, string>)[status] || status;
}

function time(value: string | null | undefined) {
  return value ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)) : '--:--:--';
}

function elapsed(startedAt: string | null, finishedAt: string | null) {
  if (!startedAt) return '未开始';
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(startedAt).getTime()) / 1000));
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function visibleContext(detail: Record<string, unknown>) {
  const labels: Record<string, string> = {
    candidate_count: '候选片段', selected_count: '已选片段', source_matches: '归档代码',
    context_files: '项目上下文', resolved_sha: '固定版本', requested_ref: '请求版本',
    term_count: '检索词', repository: '仓库', role: '版本角色', reason: '原因',
  };
  return Object.entries(detail)
    .filter(([key, value]) => labels[key] && (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'))
    .slice(0, 4)
    .map(([key, value]) => ({ label: labels[key], value: String(value) }));
}

function compactOperations(operations: InvestigationDetail['nodes'][number]['operations']) {
  const categories: Record<string, { key: string; label: string }> = {
    repository_discovery: { key: 'repository', label: '确认可用仓库' },
    git_clone: { key: 'revision', label: '锁定分析版本' },
    git_fetch: { key: 'revision', label: '锁定分析版本' },
    git_checkout: { key: 'revision', label: '锁定分析版本' },
    context_discovery: { key: 'context', label: '读取项目结构' },
    context_read: { key: 'context', label: '读取项目结构' },
    search_terms: { key: 'query', label: '提炼故障信号' },
    source_search: { key: 'search', label: '检索相关代码' },
    ai_source_focus: { key: 'ai_focus', label: 'AI 收敛代码线索' },
    source_archive: { key: 'archive', label: '归档可引用源码' },
    source_diff: { key: 'diff', label: '比较版本变化' },
    connector_collection: { key: 'runtime', label: '采集运行时证据' },
    evidence_freeze: { key: 'evidence', label: '整理可引用证据' },
    reasoning_updated: { key: 'reasoning', label: 'AI 形成当前研判' },
    conclusion_updated: { key: 'reasoning', label: 'AI 形成当前研判' },
  };
  const grouped = new Map<string, { label: string; items: typeof operations }>();
  for (const operation of operations) {
    const category = categories[operation.type];
    if (!category) continue;
    const group = grouped.get(category.key) || { label: category.label, items: [] };
    group.items.push(operation);
    grouped.set(category.key, group);
  }
  const severity = ['failed', 'blocked', 'running', 'partial', 'not_configured', 'succeeded'];
  return [...grouped.entries()].map(([key, group]) => {
    const latest = group.items[group.items.length - 1];
    const status = group.items
      .map((item) => item.status)
      .sort((left, right) => severity.indexOf(left) - severity.indexOf(right))[0] || latest.status;
    return { id: latest.id, key, label: group.label, status, display: latest.display };
  });
}

function lifecycle(history: InvestigationLiveEvent[]) {
  const labels: Record<string, string> = { started: '开始', progress: '进行中', succeeded: '完成', partial: '已收敛', blocked: '阻塞', failed: '失败', not_configured: '未配置', canceled: '已取消' };
  return history.map((event) => labels[event.phase] || event.phase).filter((item, index, all) => !index || item !== all[index - 1]);
}

export function InvestigationWorkflow({
  detail,
  selectedNodeId,
  selectedOperationId,
  onSelect,
}: {
  detail: InvestigationDetail;
  selectedNodeId: string | null;
  selectedOperationId: string | null;
  onSelect: (selection: Selection) => void;
}) {
  const waveByRevision = useMemo(() => new Map(detail.plan_history.map((item) => [item.revision, item.wave])), [detail.plan_history]);
  const waves = useMemo(() => {
    const grouped = new Map<number, InvestigationDetail['nodes']>();
    for (const node of detail.nodes) {
      const wave = waveByRevision.get(node.plan_revision) ?? node.plan_revision;
      grouped.set(wave, [...(grouped.get(wave) || []), node]);
    }
    return [...grouped.entries()].sort(([left], [right]) => left - right);
  }, [detail.nodes, waveByRevision]);
  const revisionsByWave = useMemo(() => new Map(detail.plan_history.map((item) => [item.wave, item])), [detail.plan_history]);

  return <section className="ci-flow" aria-label="调查执行流程图">
    {waves.map(([wave, nodes], waveIndex) => {
      const revision = revisionsByWave.get(wave);
      return <div className="ci-flow-wave" key={wave}>
        <header className="ci-flow-wave-heading"><div><span>调查波次 {wave + 1}</span><small>{nodes.filter((node) => node.status === 'succeeded').length}/{nodes.length} 已完成</small></div>{revision && revision.decision !== 'initial' && <p><RotateCcw size={13} />计划已更新：{revision.rationale}</p>}</header>
        <div className="ci-flow-row">
          {nodes.map((node, index) => {
            const milestones = compactOperations(node.operations);
            const current = milestones.find((operation) => ['running', 'failed', 'blocked', 'partial'].includes(operation.status)) || milestones[milestones.length - 1];
            const operationId = current?.id || node.operations.find((operation) => operation.status === 'running')?.id || node.operations[node.operations.length - 1]?.id;
            return <div className="ci-flow-segment" key={node.id}>
              <button className={`ci-flow-node ci-flow-node-${nodeTone(node.status)}${selectedNodeId === node.id ? ' is-selected' : ''}`} aria-pressed={selectedNodeId === node.id} onClick={() => onSelect({ nodeId: node.id, operationId })}>
                <span className="ci-flow-status"><i />{statusText(node.status)}<small>{elapsed(node.started_at, node.finished_at)}</small></span>
                <strong>{node.title}</strong>
                <p>{node.objective}</p>
                <footer>{current ? <><span>{current.label}</span><small>{statusText(current.status)}</small></> : <span>等待任务开始</span>}{node.ai_participated && <Bot size={13} />}</footer>
                {node.dependencies.length > 0 && <em>依赖 {node.dependencies.length} 个上游任务</em>}
              </button>
              {index < nodes.length - 1 && <div className="ci-flow-arrow" aria-hidden="true"><small>{nodes[index + 1].dependencies.length > 0 ? '依赖' : '并行'}</small><i /></div>}
            </div>;
          })}
        </div>
        {waveIndex < waves.length - 1 && <div className="ci-flow-wave-link" aria-hidden="true"><i /></div>}
      </div>;
    })}
  </section>;
}

export function InvestigationActivityLog({
  events,
  selectedNodeId,
  selectedOperationId,
  onSelect,
  onOpenEvidence,
}: {
  events: InvestigationLiveEvent[];
  selectedNodeId: string | null;
  selectedOperationId: string | null;
  onSelect: (selection: Selection) => void;
  onOpenEvidence: (id: number) => void;
}) {
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState<'all' | 'selected'>('all');
  const [phase, setPhase] = useState<'all' | 'active' | 'done'>('all');
  const [following, setFollowing] = useState(true);
  const container = useRef<HTMLDivElement>(null);
  const operationRows = useMemo(() => {
    const grouped = new Map<string, InvestigationLiveEvent[]>();
    for (const event of events) grouped.set(event.operation_id, [...(grouped.get(event.operation_id) || []), event]);
    return [...grouped.values()].map((history) => ({ event: history[history.length - 1], history })).sort((left, right) => left.event.sequence - right.event.sequence);
  }, [events]);
  const filtered = useMemo(() => operationRows.filter(({ event }) => {
    const text = `${event.display.headline} ${event.display.message}`.toLowerCase();
    const matchesScope = scope === 'all' || (selectedNodeId ? event.node_id === selectedNodeId : false);
    const matchesPhase = phase === 'all' || (phase === 'active' ? ['started', 'progress'].includes(event.phase) : !['started', 'progress'].includes(event.phase));
    return matchesScope && matchesPhase && (!query || text.includes(query.toLowerCase()));
  }), [operationRows, phase, query, scope, selectedNodeId]);
  useEffect(() => {
    if (following && container.current) container.current.scrollTop = container.current.scrollHeight;
  }, [filtered, following]);

  return <aside className="activity-console" aria-label="实时执行详情">
    <header>
      <div><span>实时执行详情</span><h2><Radio size={15} />受控操作流</h2></div>
      <button className="icon-button" aria-label="回到最新日志" onClick={() => { setFollowing(true); if (container.current) container.current.scrollTop = container.current.scrollHeight; }}><RotateCcw size={15} /></button>
    </header>
    <div className="activity-console-filters">
      <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索执行记录" aria-label="搜索执行记录" />
      <Select value={scope} onChange={(event) => setScope(event.target.value as 'all' | 'selected')} aria-label="执行范围"><option value="all">全部任务</option><option value="selected">当前任务</option></Select>
      <Select value={phase} onChange={(event) => setPhase(event.target.value as 'all' | 'active' | 'done')} aria-label="执行状态"><option value="all">全部状态</option><option value="active">执行中</option><option value="done">已结束</option></Select>
    </div>
    <div className="activity-console-list" ref={container} onScroll={(event) => {
      const element = event.currentTarget;
      setFollowing(element.scrollHeight - element.scrollTop - element.clientHeight < 24);
    }}>
      {filtered.map(({ event, history }) => <details className={`activity-line activity-line-${event.display.tone}${selectedOperationId === event.operation_id ? ' is-selected' : ''}`} key={event.operation_id} open={selectedOperationId === event.operation_id}>
        <summary onClick={() => event.node_id && onSelect({ nodeId: event.node_id, operationId: event.operation_id })}>
          <time>{time(event.occurred_at)}</time><i /><div><strong>{event.display.headline}</strong>{event.display.message && <p>{event.display.message}</p>}</div>{event.display.actor === 'ai' && <Bot size={13} />}<ChevronDown className="activity-disclosure" size={14} />
        </summary>
        <div className="activity-line-detail">{history.length > 1 && <p className="activity-lifecycle">{lifecycle(history).join(' / ')}</p>}{visibleContext(event.detail).length > 0 && <dl>{visibleContext(event.detail).map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>}{event.display.evidence_refs.length > 0 && <div>{event.display.evidence_refs.map((ref) => <button key={ref} onClick={() => onOpenEvidence(ref)}>证据 {ref}</button>)}</div>}</div>
      </details>)}
      {!filtered.length && <p className="muted">暂无符合条件的执行记录。</p>}
    </div>
    <footer><Activity size={14} /><span>{following ? '正在跟随实时记录' : '已暂停跟随，新的记录仍在接收'}</span></footer>
  </aside>;
}
