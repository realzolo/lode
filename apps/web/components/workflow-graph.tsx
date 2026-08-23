'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { AnalysisStep } from '@/lib/types';

/**
 * Interactive, dependency-free workflow graph of an analysis pipeline.
 *
 * Renders the analysis steps (receive -> git_sync -> context -> ai_analysis ->
 * memory -> conclusion) as a pannable / zoomable canvas of nodes, in the spirit
 * of a React Flow editor but with no external dependency (the package could not
 * be installed in this environment). Nodes are colored by status, clicking one
 * selects it and reveals its detail below the canvas.
 */

const NODE_W = 196;
const NODE_H = 60;
const GAP = 64;
const PAD = 36;
const MIN_SCALE = 0.4;
const MAX_SCALE = 2;

type View = { x: number; y: number; scale: number };

function statusColor(status: AnalysisStep['status']): string {
  switch (status) {
    case 'done':
      return 'var(--green)';
    case 'running':
      return 'var(--blue)';
    case 'pending':
    default:
      return 'var(--color-5)';
  }
}

function statusLabel(status: AnalysisStep['status']): string {
  switch (status) {
    case 'done':
      return 'completed';
    case 'running':
      return 'running';
    case 'pending':
    default:
      return 'pending';
  }
}

export function WorkflowGraph({ steps }: { steps: AnalysisStep[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ px: number; py: number; vx: number; vy: number } | null>(null);
  const [view, setView] = useState<View>({ x: PAD, y: PAD, scale: 1 });
  const [selected, setSelected] = useState<string | null>(null);

  const count = steps.length;
  const stageW = PAD * 2 + Math.max(0, count - 1) * (NODE_W + GAP) + NODE_W;
  const stageH = PAD * 2 + NODE_H;

  const fitView = useCallback(() => {
    const el = containerRef.current;
    if (!el || count === 0) return;
    const cw = el.clientWidth;
    const ch = el.clientHeight;
    const scale = Math.min(cw / stageW, ch / stageH, 1);
    setView({
      scale,
      x: (cw - stageW * scale) / 2,
      y: (ch - stageH * scale) / 2,
    });
  }, [count, stageW, stageH]);

  useLayoutEffect(() => {
    fitView();
  }, [fitView]);

  // Native non-passive wheel listener so we can zoom and preventDefault.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      setView((v) => {
        const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
        const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor));
        const px = cx - v.x;
        const py = cy - v.y;
        return {
          scale: next,
          x: cx - px * (next / v.scale),
          y: cy - py * (next / v.scale),
        };
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('[data-node]')) return;
    dragRef.current = { px: e.clientX, py: e.clientY, vx: view.x, vy: view.y };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    setView((v) => ({ ...v, x: d.vx + (e.clientX - d.px), y: d.vy + (e.clientY - d.py) }));
  };
  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    dragRef.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  const zoomBy = (factor: number) => {
    const el = containerRef.current;
    const cx = el ? el.clientWidth / 2 : 0;
    const cy = el ? el.clientHeight / 2 : 0;
    setView((v) => {
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor));
      const px = cx - v.x;
      const py = cy - v.y;
      return { scale: next, x: cx - px * (next / v.scale), y: cy - py * (next / v.scale) };
    });
  };

  const selectedStep = selected ? steps.find((s) => s.nodeType === selected) ?? null : null;

  if (count === 0) {
    return <p className="muted">No workflow steps.</p>;
  }

  return (
    <div>
      <div
        ref={containerRef}
        className="wf-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
      >
        <div
          ref={stageRef}
          className="wf-stage"
          style={{
            width: stageW,
            height: stageH,
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
          }}
        >
          <svg
            className="wf-edges"
            width={stageW}
            height={stageH}
            viewBox={`0 0 ${stageW} ${stageH}`}
          >
            {steps.slice(0, -1).map((s, i) => {
              const x1 = PAD + i * (NODE_W + GAP) + NODE_W;
              const y1 = PAD + NODE_H / 2;
              const x2 = PAD + (i + 1) * (NODE_W + GAP);
              const y2 = PAD + NODE_H / 2;
              const mid = (x1 + x2) / 2;
              const color = s.status === 'done' ? 'var(--green)' : 'var(--color-4)';
              return (
                <path
                  key={`edge-${s.nodeType}`}
                  d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke={color}
                  strokeWidth={2}
                />
              );
            })}
          </svg>

          {steps.map((s, i) => {
            const x = PAD + i * (NODE_W + GAP);
            const y = PAD;
            const isSel = selected === s.nodeType;
            return (
              <button
                key={s.nodeType}
                data-node
                type="button"
                className={`wf-node${isSel ? ' selected' : ''}`}
                style={{
                  left: x,
                  top: y,
                  width: NODE_W,
                  height: NODE_H,
                  borderColor: isSel ? statusColor(s.status) : 'var(--color-4)',
                  boxShadow: isSel ? `0 0 0 2px ${statusColor(s.status)}` : undefined,
                }}
                onClick={() => setSelected(isSel ? null : s.nodeType)}
              >
                <span
                  className="wf-dot"
                  style={{ background: statusColor(s.status), borderColor: statusColor(s.status) }}
                />
                <span className="wf-node-body">
                  <span className="wf-node-title">{s.title}</span>
                  <span className="wf-node-status">{statusLabel(s.status)}</span>
                </span>
              </button>
            );
          })}
        </div>

        <div className="wf-controls">
          <button type="button" className="wf-ctrl" onClick={() => zoomBy(1.2)} aria-label="Zoom in">
            +
          </button>
          <button type="button" className="wf-ctrl" onClick={() => zoomBy(1 / 1.2)} aria-label="Zoom out">
            −
          </button>
          <button type="button" className="wf-ctrl" onClick={fitView} aria-label="Fit view">
            ⊡
          </button>
        </div>
      </div>

      {selectedStep && (
        <div className="wf-detail">
          <div className="wf-detail-head">
            <span
              className="wf-dot"
              style={{
                background: statusColor(selectedStep.status),
                borderColor: statusColor(selectedStep.status),
              }}
            />
            <span className="wf-node-title">{selectedStep.title}</span>
            <span className="muted text-sm">{statusLabel(selectedStep.status)}</span>
          </div>
          {selectedStep.detail ? (
            <p className="muted" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>
              {selectedStep.detail}
            </p>
          ) : (
            <p className="muted" style={{ marginTop: 8 }}>
              No detail recorded for this step.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
