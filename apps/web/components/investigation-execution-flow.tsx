'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import {
  Bot,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Database,
  FileCode2,
  FileSearch,
  GitBranch,
  ListFilter,
  LocateFixed,
  Network,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  TerminalSquare,
  X,
} from 'lucide-react';
import { useTranslations } from 'next-intl';
import { NodeDetailDrawer } from '@/components/investigation-execution-detail';
import { Button } from '@/components/ui/button';
import type {
  InvestigationExecutionEdge,
  InvestigationExecutionGraph,
  InvestigationExecutionLane,
  InvestigationExecutionNode,
} from '@/lib/types';

const NODE_WIDTH = 244;
const NODE_HEIGHT = 130;
const STAGE_GAP = 304;
const LANE_GAP = 164;
const COMPACT_NODE_TYPES = new Set<InvestigationExecutionNode['node_type']>([
  'input',
  'operation',
  'report',
  'phase',
]);

type FocusRequest = { nodeId: string; nonce: number } | null;
type CanvasFocusRequest = { nodeIds: string[]; nonce: number } | null;

type FlowNodeData = {
  item: InvestigationExecutionNode;
  lane: InvestigationExecutionLane;
  displayTitle: string;
  purposeLabel: string;
  statusLabel: string;
  typeLabel: string;
  roundLabel: string | null;
  metricsLabel: string;
};

type ExecutionFlowNode = Node<FlowNodeData, 'execution'>;
type LaneFlowNode = Node<{ label: string; subtitle: string }, 'lane'>;
type StageFlowNode = Node<{ label: string }, 'stage'>;
type ExecutionCanvasNode = ExecutionFlowNode | LaneFlowNode | StageFlowNode;

function formatDuration(duration: number | null) {
  if (duration === null) return '-';
  if (duration < 1_000) return `${duration} ms`;
  return `${(duration / 1_000).toFixed(duration < 10_000 ? 1 : 0)} s`;
}

function statusTone(status: string) {
  if (['succeeded', 'completed', 'allowed', 'authorized'].includes(status)) return 'success';
  if (['failed', 'rejected', 'interrupted'].includes(status)) return 'danger';
  if (status === 'running') return 'active';
  return 'neutral';
}

function edgeStatus(status: string): InvestigationExecutionEdge['status'] {
  if (status === 'running') return 'active';
  if (['failed', 'rejected', 'interrupted'].includes(status)) return 'failed';
  if (['succeeded', 'completed'].includes(status)) return 'complete';
  return 'default';
}

function StatusIcon({ status }: { status: string }) {
  const tone = statusTone(status);
  if (tone === 'success') return <Check size={13} aria-hidden="true" />;
  if (tone === 'danger') return <X size={13} aria-hidden="true" />;
  if (tone === 'active') return <Play size={12} aria-hidden="true" />;
  return <Clock3 size={12} aria-hidden="true" />;
}

function NodeTypeIcon({ item }: { item: InvestigationExecutionNode }) {
  if (item.node_type === 'input') return <CircleDot size={17} />;
  if (item.node_type === 'decision') return <GitBranch size={17} />;
  if (item.node_type === 'synthesis') return <Bot size={17} />;
  if (item.node_type === 'verification') return <ShieldCheck size={17} />;
  if (item.node_type === 'report') return <FileSearch size={17} />;
  if (item.node_type === 'phase') return <RefreshCw size={17} />;
  if (item.subtitle?.includes('sql')) return <Database size={17} />;
  if (item.subtitle?.includes('logql')) return <ListFilter size={17} />;
  if (item.subtitle?.includes('command')) return <TerminalSquare size={17} />;
  if (item.subtitle?.includes('search')) return <Search size={17} />;
  if (item.subtitle === 'source_read') return <FileCode2 size={17} />;
  return <Network size={17} />;
}

function ExecutionNode({ data, selected }: NodeProps<ExecutionFlowNode>) {
  const { item, lane } = data;
  return <div className="execution-node" data-status={statusTone(item.status)} data-selected={selected ? 'true' : 'false'}>
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <div className="execution-node-heading">
      <span className="execution-node-icon"><NodeTypeIcon item={item} /></span>
      <div>
        <strong>{data.displayTitle}</strong>
        <span>{data.typeLabel} · {lane.label}</span>
      </div>
    </div>
    <p>{data.purposeLabel}</p>
    <div className="execution-node-meta">
      <span data-tone={statusTone(item.status)}><StatusIcon status={item.status} />{data.statusLabel}</span>
      <span><Clock3 size={12} />{formatDuration(item.duration_ms)}</span>
      <span>{data.metricsLabel}</span>
    </div>
    {data.roundLabel && <span className="execution-node-round">{data.roundLabel}</span>}
    <div className="execution-node-tooltip" role="tooltip">
      <strong>{data.displayTitle}</strong>
      <span>{data.purposeLabel}</span>
      {item.failure_code && <code>{item.failure_code}</code>}
    </div>
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </div>;
}

function LaneNode({ data }: NodeProps<LaneFlowNode>) {
  return <div className="execution-lane-band">
    <div className="execution-lane-label"><strong>{data.label}</strong><span>{data.subtitle}</span></div>
  </div>;
}

function StageNode({ data }: NodeProps<StageFlowNode>) {
  return <div className="execution-stage-label">{data.label}</div>;
}

const nodeTypes = { execution: ExecutionNode, lane: LaneNode, stage: StageNode };

function stageLabel(stage: InvestigationExecutionGraph['stages'][number], t: ReturnType<typeof useTranslations>) {
  if (stage.kind === 'input') return t('flowStageInput');
  if (stage.kind === 'decision' && stage.ordinal === null) return t('flowCurrentPhase');
  if (stage.kind === 'decision') return t('flowStageDecision', { round: stage.ordinal || 1 });
  if (stage.kind === 'execution') return t('flowStageExecution', { round: stage.ordinal || 1 });
  if (stage.kind === 'reporting') return t('flowStageReporting');
  return t('flowStageResult');
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

function displayNodeTitle(node: InvestigationExecutionNode, t: ReturnType<typeof useTranslations>) {
  if (node.node_type === 'decision') return t('flowDecisionRound', { round: node.round_ordinal || 1 });
  if (node.node_type === 'synthesis') return t('flowSynthesisTitle');
  if (node.node_type === 'verification') return t('flowVerificationTitle');
  if (node.node_type === 'phase') return t(`flowPhase.${node.id.slice('phase:'.length)}`);
  return node.title;
}

function displayNodePurpose(node: InvestigationExecutionNode, typeLabel: string, t: ReturnType<typeof useTranslations>) {
  if (node.node_type === 'input') return t('flowIncidentContext');
  return node.purpose || node.subtitle || typeLabel;
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
  };
  return labels[status] || status;
}

export function compactExecutionGraph(graph: InvestigationExecutionGraph): InvestigationExecutionGraph {
  const visibleNodes = graph.nodes.filter((node) => COMPACT_NODE_TYPES.has(node.node_type));
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const outgoing = new Map<string, InvestigationExecutionEdge[]>();
  for (const edge of graph.edges) {
    const values = outgoing.get(edge.source) || [];
    values.push(edge);
    outgoing.set(edge.source, values);
  }
  for (const values of outgoing.values()) values.sort((left, right) => left.target.localeCompare(right.target));

  const edges: InvestigationExecutionEdge[] = [];
  for (const source of visibleNodes) {
    const queue = [...(outgoing.get(source.id) || []).map((edge) => edge.target)];
    const visited = new Set<string>();
    const targets = new Set<string>();
    while (queue.length) {
      const target = queue.shift();
      if (!target || visited.has(target)) continue;
      visited.add(target);
      if (visibleIds.has(target)) {
        if (target !== source.id) targets.add(target);
        continue;
      }
      queue.push(...(outgoing.get(target) || []).map((edge) => edge.target));
    }
    for (const target of [...targets].sort()) {
      edges.push({
        id: `compact:${source.id}->${target}`,
        source: source.id,
        target,
        kind: 'sequence',
        status: edgeStatus(nodeById.get(target)?.status || ''),
      });
    }
  }

  const usedStageIds = new Set(visibleNodes.map((node) => node.stage_index));
  const usedLaneIds = new Set(visibleNodes.map((node) => node.lane_id));
  return {
    ...graph,
    active_node_ids: graph.active_node_ids.filter((id) => visibleIds.has(id)),
    nodes: visibleNodes,
    edges,
    stages: graph.stages.filter((stage) => usedStageIds.has(stage.index)),
    lanes: graph.lanes.filter((lane) => usedLaneIds.has(lane.id)),
  };
}

function FlowCanvas({
  graph,
  selectedNodeId,
  focusRequest,
  onSelect,
}: {
  graph: InvestigationExecutionGraph;
  selectedNodeId: string | null;
  focusRequest: CanvasFocusRequest;
  onSelect: (node: InvestigationExecutionNode) => void;
}) {
  const t = useTranslations('workbench');
  const { fitView } = useReactFlow<ExecutionCanvasNode>();
  const laneIndex = useMemo(() => new Map(graph.lanes.map((lane, index) => [lane.id, index])), [graph.lanes]);
  const stageIndex = useMemo(() => new Map(graph.stages.map((stage, index) => [stage.index, index])), [graph.stages]);
  const lanes = useMemo(() => new Map(graph.lanes.map((lane) => [lane.id, lane])), [graph.lanes]);
  const canvasWidth = Math.max(780, 180 + graph.stages.length * STAGE_GAP + 120);
  const nodes = useMemo<ExecutionCanvasNode[]>(() => {
    const laneNodes: LaneFlowNode[] = graph.lanes.map((lane, index) => ({
      id: `lane:${lane.id}`,
      type: 'lane',
      position: { x: 0, y: 42 + index * LANE_GAP },
      width: canvasWidth,
      height: LANE_GAP,
      selectable: false,
      draggable: false,
      deletable: false,
      focusable: false,
      zIndex: -1,
      style: { pointerEvents: 'none' },
      data: {
        label: lane.label,
        subtitle: lane.connector_kind || lane.subtitle || t('flowControlLane'),
      },
    }));
    const stageNodes: StageFlowNode[] = graph.stages.map((stage, index) => ({
      id: `stage:${stage.index}`,
      type: 'stage',
      position: { x: 180 + index * STAGE_GAP, y: 0 },
      width: STAGE_GAP,
      height: 42,
      selectable: false,
      draggable: false,
      deletable: false,
      focusable: false,
      zIndex: -1,
      style: { pointerEvents: 'none' },
      data: { label: stageLabel(stage, t) },
    }));
    const executionNodes: ExecutionFlowNode[] = graph.nodes.map((item) => {
      const metrics = [];
      if (item.record_count !== null) metrics.push(t('flowRecords', { count: item.record_count }));
      metrics.push(t('flowEvidenceCount', { count: item.evidence_count }));
      return {
        id: item.id,
        type: 'execution',
        position: {
          x: 180 + (stageIndex.get(item.stage_index) || 0) * STAGE_GAP + 28,
          y: 42 + (laneIndex.get(item.lane_id) || 0) * LANE_GAP + 17,
        },
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        selected: item.id === selectedNodeId,
        selectable: true,
        draggable: false,
        deletable: false,
        focusable: true,
        ariaLabel: t('flowOpenNode', { name: displayNodeTitle(item, t) }),
        data: {
          item,
          lane: lanes.get(item.lane_id) || graph.lanes[0],
          displayTitle: displayNodeTitle(item, t),
          purposeLabel: displayNodePurpose(item, nodeTypeLabel(item.node_type, t), t),
          statusLabel: statusLabel(item.status, t),
          typeLabel: nodeTypeLabel(item.node_type, t),
          roundLabel: item.round_ordinal ? t('flowRound', { round: item.round_ordinal }) : null,
          metricsLabel: metrics.join(' · ') || '-',
        },
      };
    });
    return [...laneNodes, ...stageNodes, ...executionNodes];
  }, [canvasWidth, graph.lanes, graph.nodes, graph.stages, laneIndex, lanes, selectedNodeId, stageIndex, t]);
  const edges = useMemo<Edge[]>(() => graph.edges.map((edge) => ({
    ...edge,
    type: 'smoothstep',
    animated: edge.status === 'active',
    selectable: false,
    focusable: false,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 },
    className: `execution-edge execution-edge-${edge.status}`,
  })), [graph.edges]);

  useEffect(() => {
    if (!focusRequest?.nodeIds.length) return;
    const frame = window.requestAnimationFrame(() => {
      void fitView({ nodes: focusRequest.nodeIds.map((id) => ({ id })), padding: 0.45, duration: 280, maxZoom: 1 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [fitView, focusRequest?.nonce, focusRequest?.nodeIds]);

  const height = Math.min(730, Math.max(390, graph.lanes.length * LANE_GAP + 42));

  return <div className="execution-flow-desktop">
    <div className="execution-flow-grid" style={{ height }}>
      <div className="execution-flow-canvas">
        <ReactFlow<ExecutionCanvasNode>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          deleteKeyCode={null}
          panOnDrag
          zoomOnScroll
          zoomOnPinch
          minZoom={0.45}
          maxZoom={1.6}
          fitView
          fitViewOptions={{ padding: 0.12, maxZoom: 0.9 }}
          onInit={(instance) => {
            if (graph.active_node_ids.length) {
              window.requestAnimationFrame(() => {
                void instance.fitView({ nodes: graph.active_node_ids.map((id) => ({ id })), padding: 0.45, maxZoom: 1 });
              });
            }
          }}
          onNodeClick={(_, node) => {
            if (node.type === 'execution' && node.data.item.detail_available) onSelect(node.data.item);
          }}
        >
          <Background gap={22} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  </div>;
}

function MobileFlow({ graph, selectedNodeId, onSelect }: { graph: InvestigationExecutionGraph; selectedNodeId: string | null; onSelect: (node: InvestigationExecutionNode) => void }) {
  const t = useTranslations('workbench');
  const lanes = new Map(graph.lanes.map((lane) => [lane.id, lane]));
  return <div className="execution-flow-mobile">
    {graph.stages.map((stage) => {
      const nodes = graph.nodes.filter((node) => node.stage_index === stage.index);
      if (!nodes.length) return null;
      return <section key={stage.index}>
        <h3>{stageLabel(stage, t)}</h3>
        <div>{nodes.map((node) => <button
          key={node.id}
          type="button"
          className="execution-mobile-node"
          data-status={statusTone(node.status)}
          aria-pressed={node.id === selectedNodeId}
          disabled={!node.detail_available}
          onClick={() => onSelect(node)}
        >
          <span className="execution-node-icon"><NodeTypeIcon item={node} /></span>
          <span>
            <strong>{displayNodeTitle(node, t)}</strong>
            <small>{lanes.get(node.lane_id)?.label} · {displayNodePurpose(node, nodeTypeLabel(node.node_type, t), t)}</small>
            <em><StatusIcon status={node.status} />{statusLabel(node.status, t)} · {formatDuration(node.duration_ms)} · {node.record_count !== null ? `${t('flowRecords', { count: node.record_count })} · ` : ''}{t('flowEvidenceCount', { count: node.evidence_count })}</em>
          </span>
          {node.detail_available && <ChevronRight size={16} />}
        </button>)}</div>
      </section>;
    })}
  </div>;
}

export function InvestigationExecutionFlow({
  investigationId,
  graph,
  selectedNodeId,
  onSelectedNodeIdChange,
  focusRequest,
}: {
  investigationId: number | string;
  graph: InvestigationExecutionGraph | null;
  selectedNodeId: string | null;
  onSelectedNodeIdChange: (nodeId: string | null) => void;
  focusRequest: FocusRequest;
}) {
  const t = useTranslations('workbench');
  const [mode, setMode] = useState<'compact' | 'full'>('compact');
  const [canvasFocus, setCanvasFocus] = useState<CanvasFocusRequest>(null);
  const viewGraph = useMemo(() => graph && mode === 'compact' ? compactExecutionGraph(graph) : graph, [graph, mode]);
  const visibleIds = useMemo(() => new Set(viewGraph?.nodes.map((node) => node.id) || []), [viewGraph]);
  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId) || null;

  const select = useCallback((node: InvestigationExecutionNode) => {
    if (node.detail_available) onSelectedNodeIdChange(node.id);
  }, [onSelectedNodeIdChange]);

  useEffect(() => {
    if (!focusRequest || !graph) return;
    if (mode === 'compact' && !visibleIds.has(focusRequest.nodeId)) setMode('full');
    setCanvasFocus({ nodeIds: [focusRequest.nodeId], nonce: focusRequest.nonce });
  }, [focusRequest, graph, mode, visibleIds]);

  useEffect(() => {
    if (selectedNodeId && graph && !graph.nodes.some((node) => node.id === selectedNodeId)) {
      onSelectedNodeIdChange(null);
    }
  }, [graph, onSelectedNodeIdChange, selectedNodeId]);

  const locateCurrent = useCallback(() => {
    if (!graph) return;
    const targetIds = graph.active_node_ids.length
      ? graph.active_node_ids
      : graph.nodes.length
      ? [graph.nodes[graph.nodes.length - 1].id]
      : [];
    if (mode === 'compact' && targetIds.some((id) => !visibleIds.has(id))) setMode('full');
    setCanvasFocus((current) => ({ nodeIds: targetIds, nonce: (current?.nonce || 0) + 1 }));
  }, [graph, mode, visibleIds]);

  function changeMode(nextMode: 'compact' | 'full') {
    setMode(nextMode);
    if (nextMode === 'compact' && selectedNodeId && !COMPACT_NODE_TYPES.has(selectedNode?.node_type || 'phase')) {
      onSelectedNodeIdChange(null);
    }
  }

  if (!graph || !viewGraph) return <section id="investigation-flow" className="execution-flow-empty"><RefreshCw size={17} />{t('flowLoading')}</section>;
  if (!graph.nodes.length) return <section id="investigation-flow" className="execution-flow-empty">{t('flowEmpty')}</section>;
  const rounds = new Set(graph.nodes.map((node) => node.round_ordinal).filter((value) => value !== null)).size;
  const operationCount = graph.nodes.filter((node) => node.node_type === 'operation').length;
  const evidenceCount = new Set(graph.nodes.flatMap((node) => node.evidence_refs)).size;

  return <section id="investigation-flow" className="execution-flow-section">
    <header className="execution-flow-summary">
      <div>
        <p className="eyebrow">{t('investigationProcess')}</p>
        <h2>{t(`flowPhase.${graph.phase}`)}</h2>
        <p>{t('flowProcessCounts', { rounds, operations: operationCount, evidence: evidenceCount })}</p>
      </div>
      <div className="execution-flow-summary-actions">
        <div className="execution-mode-control" role="group" aria-label={t('flowViewMode')}>
          <button type="button" aria-pressed={mode === 'compact'} onClick={() => changeMode('compact')}>{t('flowCompactMode')}</button>
          <button type="button" aria-pressed={mode === 'full'} onClick={() => changeMode('full')}>{t('flowFullMode')}</button>
        </div>
        <Button size="icon" variant="outline" aria-label={t('flowLocateCurrent')} title={t('flowLocateCurrent')} onClick={locateCurrent}><LocateFixed size={16} /></Button>
      </div>
    </header>
    <ReactFlowProvider>
      <FlowCanvas graph={viewGraph} selectedNodeId={selectedNodeId} focusRequest={canvasFocus} onSelect={select} />
    </ReactFlowProvider>
    <MobileFlow graph={viewGraph} selectedNodeId={selectedNodeId} onSelect={select} />
    {graph.unused_connectors.length > 0 && <details className="execution-unused">
      <summary>{t('flowUnusedConnectors', { count: graph.unused_connectors.length })}</summary>
      <div>{graph.unused_connectors.map((connector) => <article key={connector.snapshot_id}>
        <div><strong>{connector.name}</strong><span>{connector.kind}</span></div>
        <small>{connector.reason_code ? t('flowUnusedReason', { reason: connector.reason_code }) : t('flowUnusedNoRecord')}</small>
      </article>)}</div>
    </details>}
    <NodeDetailDrawer
      investigationId={investigationId}
      node={selectedNode}
      eventCursor={graph.event_cursor}
      onOpenChange={(open) => { if (!open) onSelectedNodeIdChange(null); }}
    />
  </section>;
}
