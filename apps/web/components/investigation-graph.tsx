'use client';

import { useMemo } from 'react';
import { Background, Controls, MarkerType, MiniMap, ReactFlow, type Edge, type Node } from '@xyflow/react';
import type { InvestigationDetail } from '@/lib/api';

type FlowData = {
  label: string;
  title: string;
  subtitle: string;
  status: string;
  evidenceRefs: number[];
};

function statusTone(status: string) {
  if (['succeeded', 'completed', 'confirmed', 'supported', 'resolved'].includes(status)) return 'ok';
  if (['failed', 'violated', 'refuted'].includes(status)) return 'error';
  if (['partial', 'blocked', 'provisional', 'insufficient', 'unavailable', 'open', 'required'].includes(status)) return 'warn';
  return 'pending';
}

function reasoningGraph(detail: InvestigationDetail, selectedId: string | null): { nodes: Node<FlowData>[]; edges: Edge[] } {
  const columns: Record<string, number> = { fact: 0, impact: 0, hypothesis: 1, counter_evidence: 1, evidence_gap: 1, conclusion: 2 };
  const rowsByColumn = new Map<number, number>();
  const nodes = detail.reasoning_path.map((finding) => {
    const column = columns[finding.kind] ?? 1;
    const row = rowsByColumn.get(column) ?? 0;
    rowsByColumn.set(column, row + 1);
    const id = `finding-${finding.id}`;
    return {
      id,
      position: { x: column * 310, y: row * 150 },
      data: {
        label: finding.text,
        title: finding.text,
        subtitle: finding.kind === 'fact' ? '已证实事实' : finding.kind === 'conclusion' ? '当前结论' : finding.kind === 'evidence_gap' ? '待补充证据' : finding.kind === 'counter_evidence' ? '反证' : '待验证机制',
        status: finding.status,
        evidenceRefs: finding.evidence_refs,
      },
      className: `investigation-flow-node investigation-flow-node-${statusTone(finding.status)}${selectedId === id ? ' is-selected' : ''}`,
      draggable: false,
    };
  });
  const known = new Set(nodes.map((node) => node.id));
  const edges = detail.reasoning_edges
    .map((edge) => ({
      id: `${edge.from}-${edge.to}`,
      source: `finding-${edge.from}`,
      target: `finding-${edge.to}`,
      label: edge.relation === 'supports' ? '支持' : edge.relation === 'contradicts' ? '反证' : edge.relation === 'caused_by' ? '归因' : '待验证',
      type: 'smoothstep',
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    }))
    .filter((edge) => known.has(edge.source) && known.has(edge.target));
  return { nodes, edges };
}

export function InvestigationGraph({
  detail,
  selectedId,
  onOpenEvidence,
}: {
  detail: InvestigationDetail;
  selectedId: string | null;
  onOpenEvidence: (id: number) => void;
}) {
  const graph = useMemo(() => reasoningGraph(detail, selectedId), [detail, selectedId]);
  const empty = graph.nodes.length === 0;
  if (empty) return <div className="investigation-graph-empty"><div><strong>等待第一组交叉证据</strong><p>AI 会在运行时事实与固定版本源码能够互相验证后，才生成因果路径；不会用空关系图制造确定感。</p></div></div>;
  return <div className="investigation-graph investigation-graph-reasoning" aria-label="证据推理图">
    <ReactFlow
      nodes={graph.nodes}
      edges={graph.edges}
      fitView
      minZoom={0.35}
      maxZoom={1.6}
      nodesConnectable={false}
      nodesDraggable={false}
      elementsSelectable
      onNodeClick={(_, node) => {
        const data = node.data as FlowData;
        if (data.evidenceRefs[0]) onOpenEvidence(data.evidenceRefs[0]);
      }}
    >
      <Background gap={22} size={1} />
      <MiniMap pannable zoomable nodeColor={(node) => node.className?.includes('error') ? '#ef6b73' : node.className?.includes('warn') ? '#e3a83d' : '#39c69b'} />
      <Controls showInteractive={false} />
    </ReactFlow>
  </div>;
}
