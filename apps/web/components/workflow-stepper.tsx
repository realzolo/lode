'use client';

import { Fragment } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Inbox,
  GitBranch,
  Layers,
  ServerCog,
  BrainCircuit,
  MemoryStick,
  Flag,
  Check,
  X,
  Loader2,
  Circle,
  Minus,
} from 'lucide-react';
import type { AnalysisStep } from '@/lib/types';

/**
 * Horizontal pipeline stepper for a root-cause analysis run.
 *
 * Always renders the fixed pipeline (receive -> git_sync -> context -> service_snapshot
 * -> ai_analysis -> experience -> conclusion) so even a `pending` analysis shows what
 * *will* happen instead of an empty "No workflow steps." Live `AnalysisStep`
 * records from the API are overlaid on top of the static skeleton; any node
 * without a record is shown as `pending` (排队中).
 *
 * No pan/zoom — the whole pipeline fits the card at a glance. Clicking a node
 * reveals its detail text below the row.
 */

type StepState = AnalysisStep['status'];

interface PipelineNode {
  nodeType: AnalysisStep['nodeType'];
  icon: LucideIcon;
  title: string;
}

export const PIPELINE: PipelineNode[] = [
  { nodeType: 'receive', icon: Inbox, title: '接收告警' },
  { nodeType: 'git_sync', icon: GitBranch, title: '同步源码' },
  { nodeType: 'context', icon: Layers, title: '收集上下文' },
  { nodeType: 'service_snapshot', icon: ServerCog, title: '服务快照' },
  { nodeType: 'ai_analysis', icon: BrainCircuit, title: 'AI 根因分析' },
  { nodeType: 'experience', icon: MemoryStick, title: '匹配经验' },
  { nodeType: 'conclusion', icon: Flag, title: '生成结论' },
];

const STATUS_TEXT: Record<StepState, string> = {
  done: '已完成',
  running: '运行中',
  pending: '排队中',
  failed: '失败',
  skipped: '已跳过',
};

// Glyph drawn inside each node's status ring.
function StepGlyph({ state, size = 18 }: { state: StepState; size?: number }) {
  switch (state) {
    case 'done':
      return <Check size={size} strokeWidth={2.5} />;
    case 'running':
      return <Loader2 size={size} className="wf-spin" />;
    case 'failed':
      return <X size={size} strokeWidth={2.5} />;
    case 'skipped':
      return <Minus size={size} strokeWidth={2.5} />;
    case 'pending':
    default:
      return <Circle size={size - 4} />;
  }
}

// Connector state derived from the node to its left and the node to its right.
type ConnectorState = 'done' | 'active' | 'idle';

function connectorState(left: StepState, right: StepState): ConnectorState {
  if (left === 'done' && right === 'done') return 'done';
  if (left === 'done' && right === 'running') return 'active';
  return 'idle';
}

export function WorkflowStepper({
  steps,
  selected,
  onSelect,
}: {
  steps: AnalysisStep[];
  selected: AnalysisStep['nodeType'];
  onSelect: (nodeType: AnalysisStep['nodeType']) => void;
}) {

  // Overlay live records onto the fixed pipeline. Any node missing a record
  // (e.g. a not-yet-run step, or a whole pending analysis) defaults to pending.
  const byType = new Map(steps.map((s) => [s.nodeType, s]));
  const merged: AnalysisStep[] = PIPELINE.map((node) => {
    const found = byType.get(node.nodeType);
    // `node_type` is a stable backend identifier, not UI copy. Keep all
    // display labels in the pipeline definition so API values can never leak
    // into the operator-facing workflow.
    if (found) return found;
    return { nodeType: node.nodeType, status: 'pending' as StepState };
  });

  return (
    <div>
      <div className="wf-stepper" role="list" aria-label="分析工作流步骤">
        {merged.map((step, i) => {
          const node = PIPELINE[i];
          const Icon = node.icon;
          const isSel = selected === step.nodeType;
          const next = merged[i + 1];
          return (
            <Fragment key={step.nodeType}>
              <button
                type="button"
                role="listitem"
                className="wf-step"
                data-state={step.status}
                aria-pressed={isSel}
                onClick={() => onSelect(step.nodeType)}
              >
                <span className="wf-step-icon">
                  <StepGlyph state={step.status} />
                </span>
                <span className="wf-step-body">
                  <span className="wf-step-title">{node.title}</span>
                  <span className="wf-step-status">{STATUS_TEXT[step.status]}</span>
                </span>
                {/* Tiny type glyph kept faint so each step is still identifiable */}
                <span className="wf-step-kind" aria-hidden>
                  <Icon size={13} />
                </span>
              </button>
              {next && (
                <span
                  className="wf-connector"
                  data-state={connectorState(step.status, next.status)}
                  aria-hidden
                >
                  <svg width="100%" height="12" viewBox="0 0 32 12" preserveAspectRatio="none">
                    <line className="wf-line" x1="0" y1="6" x2="26" y2="6" />
                    <path className="wf-arrow" d="M24 2 L30 6 L24 10" fill="none" />
                  </svg>
                </span>
              )}
            </Fragment>
          );
        })}
      </div>

    </div>
  );
}
