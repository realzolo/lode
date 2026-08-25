'use client';

import { Bot, ChevronDown, CircleDot, Clock3, Database, Wrench } from 'lucide-react';
import type { InvestigationDetail, InvestigationOperation, InvestigationStepStatus } from '@/lib/api';

function statusText(status: InvestigationStepStatus) {
  return ({ queued: '等待', running: '执行中', succeeded: '完成', partial: '部分完成', blocked: '阻塞', failed: '失败', canceled: '取消' } as Record<string, string>)[status] || status;
}

function duration(value: number | null) {
  if (value === null) return '--';
  if (value < 1_000) return `${value} ms`;
  const seconds = Math.round(value / 1_000);
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function actorIcon(actor: InvestigationOperation['actor']) {
  if (actor === 'ai') return <Bot size={14} />;
  if (actor === 'collector') return <Database size={14} />;
  return <Wrench size={14} />;
}

export function InvestigationWorkflow({ detail, selectedOperationId, onSelectOperation }: { detail: InvestigationDetail; selectedOperationId: string | null; onSelectOperation: (id: string) => void }) {
  const byStep = new Map<number, InvestigationOperation[]>();
  for (const operation of detail.operations) byStep.set(operation.step_id, [...(byStep.get(operation.step_id) || []), operation]);
  return <div className="serial-workflow" aria-label="串行调查步骤">
    {detail.steps.map((step) => <section className={`serial-step serial-step-${step.status}`} key={step.id}>
      <div className="serial-step-marker"><span>{step.ordinal}</span><i /></div>
      <div className="serial-step-body">
        <header><div><small>{step.kind}</small><h3>{step.title}</h3></div><span className={`serial-status serial-status-${step.status}`}>{statusText(step.status)}</span></header>
        <p>{step.objective}</p>
        <dl className="serial-step-contract"><div><dt>选择依据</dt><dd>{step.selection_reason}</dd></div><div><dt>预期证据</dt><dd>{step.expected_evidence}</dd></div>{step.result && <div><dt>步骤结果</dt><dd>{step.result}</dd></div>}</dl>
        <div className="serial-operations">{(byStep.get(step.db_id) || []).map((operation) => <Operation key={operation.id} operation={operation} selected={selectedOperationId === operation.id} onSelect={() => onSelectOperation(operation.id)} />)}</div>
        {!byStep.has(step.db_id) && <p className="serial-step-empty">等待受控操作</p>}
      </div>
    </section>)}
  </div>;
}

function Operation({ operation, selected, onSelect }: { operation: InvestigationOperation; selected: boolean; onSelect: () => void }) {
  return <details className={`serial-operation${selected ? ' is-selected' : ''}`} open={selected || operation.status === 'running'}>
    <summary onClick={onSelect}><span className="serial-operation-icon">{actorIcon(operation.actor)}</span><div><strong>{operation.title}</strong><small>{operation.kind}</small></div><span className={`serial-status serial-status-${operation.status}`}>{statusText(operation.status)}</span><span className="serial-duration"><Clock3 size={12} />{duration(operation.duration_ms)}</span><ChevronDown size={14} /></summary>
    <div className="serial-operation-detail">
      <section><span>目的</span><p>{operation.purpose}</p></section>
      <section><span>脱敏输入</span><pre>{JSON.stringify(operation.input, null, 2)}</pre></section>
      <section><span>执行过程</span><ol>{operation.events.map((event) => <li key={event.sequence}><CircleDot size={12} /><div><strong>{event.message}</strong><small>{event.kind} · #{event.sequence}</small>{Object.keys(event.detail).length > 0 && <pre>{JSON.stringify(event.detail, null, 2)}</pre>}</div></li>)}</ol></section>
      <section><span>实际结果</span><p>{operation.result || '尚未产生结果'}</p>{Object.keys(operation.metrics).length > 0 && <pre>{JSON.stringify(operation.metrics, null, 2)}</pre>}</section>
      {operation.failure && <section className="serial-operation-failure"><span>失败原因</span><p>{operation.failure.code}: {operation.failure.detail}</p></section>}
      {operation.evidence_refs.length > 0 && <section><span>归档证据</span><p className="mono">{operation.evidence_refs.map((ref) => `#${ref}`).join(' · ')}</p></section>}
    </div>
  </details>;
}
