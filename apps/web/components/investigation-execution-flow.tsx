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
  AlertTriangle,
  Bot,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Code2,
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
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Select } from '@/components/ui/select';
import {
  apiErrorMessage,
  fetchInvestigationExecutionArtifact,
  fetchInvestigationExecutionNode,
} from '@/lib/api';
import type {
  InvestigationExecutionArtifactPage,
  InvestigationExecutionGraph,
  InvestigationExecutionLane,
  InvestigationExecutionNode,
  InvestigationExecutionNodeDetail,
} from '@/lib/types';

const NODE_WIDTH = 244;
const NODE_HEIGHT = 130;
const STAGE_GAP = 304;
const LANE_GAP = 164;

type FlowNodeData = {
  item: InvestigationExecutionNode;
  lane: InvestigationExecutionLane;
  statusLabel: string;
  typeLabel: string;
  roundLabel: string | null;
  metricsLabel: string;
};

type ExecutionFlowNode = Node<FlowNodeData, 'execution'>;

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
  return <div
    className="execution-node"
    data-status={statusTone(item.status)}
    data-selected={selected ? 'true' : 'false'}
  >
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <div className="execution-node-heading">
      <span className="execution-node-icon"><NodeTypeIcon item={item} /></span>
      <div>
        <strong>{item.title}</strong>
        <span>{data.typeLabel} · {lane.label}</span>
      </div>
    </div>
    <p>{item.purpose || item.subtitle || data.typeLabel}</p>
    <div className="execution-node-meta">
      <span data-tone={statusTone(item.status)}><StatusIcon status={item.status} />{data.statusLabel}</span>
      <span><Clock3 size={12} />{formatDuration(item.duration_ms)}</span>
      <span>{data.metricsLabel}</span>
    </div>
    {data.roundLabel && <span className="execution-node-round">{data.roundLabel}</span>}
    <div className="execution-node-tooltip" role="tooltip">
      <strong>{item.title}</strong>
      <span>{item.purpose || item.subtitle || data.typeLabel}</span>
      {item.failure_code && <code>{item.failure_code}</code>}
    </div>
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </div>;
}

const nodeTypes = { execution: ExecutionNode };

function stageLabel(
  stage: InvestigationExecutionGraph['stages'][number],
  t: ReturnType<typeof useTranslations>,
) {
  if (stage.kind === 'input') return t('flowStageInput');
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

function FlowCanvas({
  graph,
  selectedNodeId,
  onSelect,
}: {
  graph: InvestigationExecutionGraph;
  selectedNodeId: string | null;
  onSelect: (node: InvestigationExecutionNode) => void;
}) {
  const t = useTranslations('workbench');
  const { fitView } = useReactFlow<ExecutionFlowNode>();
  const laneIndex = useMemo(
    () => new Map(graph.lanes.map((lane, index) => [lane.id, index])),
    [graph.lanes],
  );
  const stageIndex = useMemo(
    () => new Map(graph.stages.map((stage, index) => [stage.index, index])),
    [graph.stages],
  );
  const lanes = useMemo(() => new Map(graph.lanes.map((lane) => [lane.id, lane])), [graph.lanes]);
  const nodes = useMemo<ExecutionFlowNode[]>(() => graph.nodes.map((item) => ({
    id: item.id,
    type: 'execution',
    position: {
      x: (stageIndex.get(item.stage_index) || 0) * STAGE_GAP + 28,
      y: (laneIndex.get(item.lane_id) || 0) * LANE_GAP + 17,
    },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    selected: item.id === selectedNodeId,
    selectable: true,
    draggable: false,
    deletable: false,
    focusable: true,
    ariaLabel: t('flowOpenNode', { name: item.title }),
    data: {
      item,
      lane: lanes.get(item.lane_id) || graph.lanes[0],
      statusLabel: statusLabel(item.status, t),
      typeLabel: nodeTypeLabel(item.node_type, t),
      roundLabel: item.round_ordinal ? t('flowRound', { round: item.round_ordinal }) : null,
      metricsLabel: item.record_count !== null
        ? t('flowRecords', { count: item.record_count })
        : t('flowEvidenceCount', { count: item.evidence_count }),
    },
  })), [graph.lanes, graph.nodes, laneIndex, lanes, selectedNodeId, stageIndex, t]);
  const edges = useMemo<Edge[]>(() => graph.edges.map((edge) => ({
    ...edge,
    type: 'smoothstep',
    animated: edge.status === 'active',
    selectable: false,
    focusable: false,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 },
    className: `execution-edge execution-edge-${edge.status}`,
  })), [graph.edges]);
  const locateCurrent = useCallback(() => {
    const targets = graph.active_node_ids.length
      ? graph.active_node_ids
      : graph.nodes.length
      ? [graph.nodes[graph.nodes.length - 1].id]
      : [];
    void fitView({ nodes: targets.map((id) => ({ id })), padding: 0.45, duration: 280, maxZoom: 1 });
  }, [fitView, graph.active_node_ids, graph.nodes]);
  const minWidth = Math.max(780, graph.stages.length * STAGE_GAP + 120);
  const height = Math.min(730, Math.max(390, graph.lanes.length * LANE_GAP + 34));

  return <div className="execution-flow-desktop">
    <div className="execution-flow-toolbar">
      <div className="execution-stage-axis" style={{ minWidth }}>
        {graph.stages.map((stage) => <span key={stage.index}>{stageLabel(stage, t)}</span>)}
      </div>
      <Button
        size="icon"
        variant="outline"
        aria-label={t('flowLocateCurrent')}
        title={t('flowLocateCurrent')}
        onClick={locateCurrent}
      ><LocateFixed size={16} /></Button>
    </div>
    <div className="execution-flow-grid" style={{ height }}>
      <aside className="execution-lanes" aria-label={t('flowConnectorLanes')}>
        {graph.lanes.map((lane) => <div key={lane.id} style={{ height: LANE_GAP }}>
          <strong>{lane.label}</strong>
          <span>{lane.connector_kind || lane.subtitle || t('flowControlLane')}</span>
        </div>)}
      </aside>
      <div className="execution-flow-canvas">
        <ReactFlow<ExecutionFlowNode>
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
          defaultViewport={{ x: 12, y: 8, zoom: 0.8 }}
          onInit={(instance) => {
            if (graph.active_node_ids.length) {
              void instance.fitView({
                nodes: graph.active_node_ids.map((id) => ({ id })),
                padding: 0.45,
                maxZoom: 1,
              });
            }
          }}
          onNodeClick={(_, node) => node.data.item.detail_available && onSelect(node.data.item)}
        >
          <Background gap={22} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  </div>;
}

function MobileFlow({
  graph,
  selectedNodeId,
  onSelect,
}: {
  graph: InvestigationExecutionGraph;
  selectedNodeId: string | null;
  onSelect: (node: InvestigationExecutionNode) => void;
}) {
  const t = useTranslations('workbench');
  const lanes = new Map(graph.lanes.map((lane) => [lane.id, lane]));
  return <div className="execution-flow-mobile">
    {graph.stages.map((stage) => {
      const nodes = graph.nodes.filter((node) => node.stage_index === stage.index);
      if (!nodes.length) return null;
      return <section key={stage.index}>
        <h3>{stageLabel(stage, t)}</h3>
        <div>
          {nodes.map((node) => <button
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
              <strong>{node.title}</strong>
              <small>{lanes.get(node.lane_id)?.label} · {node.purpose || node.subtitle}</small>
              <em><StatusIcon status={node.status} />{statusLabel(node.status, t)} · {formatDuration(node.duration_ms)}</em>
            </span>
            {node.detail_available && <ChevronRight size={16} />}
          </button>)}
        </div>
      </section>;
    })}
  </div>;
}

export function InvestigationExecutionFlow({
  investigationId,
  graph,
}: {
  investigationId: number | string;
  graph: InvestigationExecutionGraph | null;
}) {
  const t = useTranslations('workbench');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const select = useCallback((node: InvestigationExecutionNode) => {
    if (node.detail_available) setSelectedNodeId(node.id);
  }, []);
  if (!graph) return <div className="execution-flow-empty"><RefreshCw size={17} />{t('flowLoading')}</div>;
  if (!graph.nodes.length) return <div className="execution-flow-empty">{t('flowEmpty')}</div>;
  return <section className="execution-flow-section">
    <header className="execution-flow-summary">
      <div>
        <p className="eyebrow">{t('flowCurrentPhase')}</p>
        <h2>{t(`flowPhase.${graph.phase}`)}</h2>
      </div>
      <div className="execution-status-legend" aria-label={t('flowStatusLegend')}>
        {['running', 'succeeded', 'failed', 'queued'].map((status) => <span key={status} data-tone={statusTone(status)}><i />{statusLabel(status, t)}</span>)}
      </div>
    </header>
    <ReactFlowProvider>
      <FlowCanvas graph={graph} selectedNodeId={selectedNodeId} onSelect={select} />
    </ReactFlowProvider>
    <MobileFlow graph={graph} selectedNodeId={selectedNodeId} onSelect={select} />
    {graph.unused_connectors.length > 0 && <details className="execution-unused">
      <summary>{t('flowUnusedConnectors', { count: graph.unused_connectors.length })}</summary>
      <div>{graph.unused_connectors.map((connector) => <article key={connector.snapshot_id}>
        <div><strong>{connector.name}</strong><span>{connector.kind} · {connector.allowed_languages.join(', ')}</span></div>
        <small>{connector.reason_code ? t('flowUnusedReason', { reason: connector.reason_code }) : t('flowUnusedNoRecord')}</small>
      </article>)}</div>
    </details>}
    <NodeDetailDrawer
      investigationId={investigationId}
      nodeId={selectedNodeId}
      onOpenChange={(open) => { if (!open) setSelectedNodeId(null); }}
    />
  </section>;
}

function NodeDetailDrawer({
  investigationId,
  nodeId,
  onOpenChange,
}: {
  investigationId: number | string;
  nodeId: string | null;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const [detail, setDetail] = useState<InvestigationExecutionNodeDetail | null>(null);
  const [error, setError] = useState('');
  const [artifactId, setArtifactId] = useState<number | null>(null);
  const [page, setPage] = useState<InvestigationExecutionArtifactPage | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    if (!nodeId) {
      setDetail(null);
      setPage(null);
      return;
    }
    let current = true;
    setDetail(null);
    setError('');
    void fetchInvestigationExecutionNode(investigationId, nodeId)
      .then((value) => {
        if (!current) return;
        setDetail(value);
        setArtifactId(value.result_page?.artifact_id || value.artifacts[0]?.id || null);
        setPage(value.result_page);
      })
      .catch((cause) => current && setError(apiErrorMessage(cause, tc('requestFailed'))));
    return () => { current = false; };
  }, [investigationId, nodeId, tc]);

  const selectArtifact = useCallback(async (nextArtifactId: number) => {
    if (!nodeId) return;
    setArtifactId(nextArtifactId);
    setPage(null);
    try {
      setPage(await fetchInvestigationExecutionArtifact(investigationId, nodeId, nextArtifactId));
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    }
  }, [investigationId, nodeId, tc]);

  const loadMore = useCallback(async () => {
    if (!nodeId || !artifactId || page?.next_after_index === null || page?.next_after_index === undefined) return;
    setLoadingMore(true);
    try {
      const next = await fetchInvestigationExecutionArtifact(
        investigationId,
        nodeId,
        artifactId,
        page.next_after_index,
      );
      setPage((current) => current ? { ...next, items: [...current.items, ...next.items] } : next);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setLoadingMore(false);
    }
  }, [artifactId, investigationId, nodeId, page?.next_after_index, tc]);

  return <Dialog open={nodeId !== null} onOpenChange={onOpenChange}>
    <DialogContent variant="drawer" className="execution-detail-drawer max-w-3xl overflow-hidden p-0">
      <DialogHeader className="border-b px-6 py-5">
        <DialogTitle>{detail?.title || t('flowNodeDetails')}</DialogTitle>
        <DialogDescription>{detail ? `${nodeTypeLabel(detail.node_type, t)} · ${statusLabel(detail.status, t)}` : t('flowDetailLoading')}</DialogDescription>
      </DialogHeader>
      <div className="execution-detail-scroll">
        {error && <div className="execution-detail-error"><AlertTriangle size={16} />{error}</div>}
        {!detail && !error && <div className="execution-detail-loading"><RefreshCw size={16} />{t('flowDetailLoading')}</div>}
        {detail && <>
          <DetailText title={t('flowPurpose')} value={detail.overview.purpose} />
          <DetailText title={t('flowSelectionReason')} value={detail.overview.selection_reason} />
          <DetailText title={t('flowExpectedEvidence')} value={detail.overview.expected_evidence} />
          {detail.query && <section className="execution-detail-section">
            <h3>{detail.query.state === 'proposed' ? t('flowProposedQuery') : t('flowActualQuery')}</h3>
            <dl className="execution-detail-facts">
              <div><dt>{t('flowQueryLanguage')}</dt><dd>{String(detail.query.language || '-')}</dd></div>
              <div><dt>{t('flowQueryState')}</dt><dd>{t(`flowQueryStates.${String(detail.query.state)}`)}</dd></div>
              <div><dt>{t('flowQueryWindow')}</dt><dd><JsonValue value={detail.query.requested_window} /></dd></div>
              <div><dt>{t('flowQueryLimit')}</dt><dd>{String(detail.query.requested_limit || '-')}</dd></div>
            </dl>
            {detail.query.effective_action != null && <div className="execution-query-block"><span>{t('flowAuthorizedQuery')}</span><JsonValue value={detail.query.effective_action} code /></div>}
            {detail.query.proposed_payload != null && <div className="execution-query-block"><span>{t('flowOriginalQuery')}</span><JsonValue value={detail.query.proposed_payload} code /></div>}
          </section>}
          {detail.authorization && <section className="execution-detail-section">
            <h3>{t('flowAuthorization')}</h3>
            <JsonValue value={detail.authorization} />
          </section>}
          {detail.artifacts.length > 0 && <section className="execution-detail-section">
            <div className="execution-detail-section-heading">
              <h3>{t('flowResponse')}</h3>
              {detail.artifacts.length > 1 && <Select
                aria-label={t('flowSelectArtifact')}
                value={artifactId ? String(artifactId) : ''}
                onChange={(event) => void selectArtifact(Number(event.target.value))}
              >{detail.artifacts.map((artifact) => <option key={artifact.id} value={artifact.id}>{artifact.kind} · {artifact.record_count ?? '-'}</option>)}</Select>}
            </div>
            {page ? <ArtifactResult page={page} /> : <div className="execution-detail-loading">{t('flowResponseLoading')}</div>}
            {page && page.next_after_index !== null && <Button variant="outline" loading={loadingMore} onClick={() => void loadMore()}>{t('loadMore')}</Button>}
          </section>}
          {detail.execution && <section className="execution-detail-section">
            <h3>{t('flowExecution')}</h3>
            <JsonValue value={detail.execution} />
          </section>}
          {(detail.execution?.failure_code || detail.authorization?.rejection_code) && <section className="execution-detail-section execution-detail-failure">
            <h3>{t('flowFailure')}</h3>
            <JsonValue value={{
              failure_code: detail.execution?.failure_code,
              failure_detail: detail.execution?.failure_detail,
              rejection_code: detail.authorization?.rejection_code,
              rejection_detail: detail.authorization?.rejection_detail,
            }} />
          </section>}
          {(detail.events.length > 0 || detail.authorization) && <section className="execution-detail-section">
            <h3>{t('flowAuditChain')}</h3>
            <ol className="execution-audit-chain">
              {detail.authorization && <li><ShieldCheck size={15} /><div><strong>{t('flowAuthorization')}</strong><JsonValue value={detail.authorization} /></div></li>}
              {detail.events.map((event, index) => <li key={String(event.sequence || index)}><CircleDot size={15} /><div><strong>{String(event.message || event.event_name)}</strong><small>{String(event.occurred_at || '')}</small><JsonValue value={event.detail} /></div></li>)}
            </ol>
          </section>}
        </>}
      </div>
    </DialogContent>
  </Dialog>;
}

function DetailText({ title, value }: { title: string; value: unknown }) {
  if (value === null || value === undefined || value === '') return null;
  return <section className="execution-detail-section"><h3>{title}</h3><p>{String(value)}</p></section>;
}

function JsonValue({ value, code = false }: { value: unknown; code?: boolean }) {
  if (value === null || value === undefined) return <span>-</span>;
  if (typeof value === 'string') return code ? <pre>{value}</pre> : <p>{value}</p>;
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

function ArtifactResult({ page }: { page: InvestigationExecutionArtifactPage }) {
  const t = useTranslations('workbench');
  const rows = page.items.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
  if (page.artifact_kind.includes('sql') && rows.length) {
    const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
    return <div className="execution-result-table"><table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}><CellValue value={row[column]} /></td>)}</tr>)}</tbody></table></div>;
  }
  if (page.artifact_kind.includes('log') && rows.length) {
    const ordered = [...rows].sort((left, right) => String(left.timestamp || left.time || '').localeCompare(String(right.timestamp || right.time || '')));
    return <ol className="execution-log-list">{ordered.map((row, index) => <li key={index}><time>{formatLogTimestamp(row.timestamp || row.time)}</time><code>{String(row.message || row.line || row.value || JSON.stringify(row))}</code></li>)}</ol>;
  }
  if ((page.artifact_kind.includes('search') || page.artifact_kind.includes('opensearch') || page.artifact_kind.includes('elasticsearch')) && rows.length) {
    return <div className="execution-record-list">{page.metadata && Object.keys(page.metadata).length > 0 && <JsonValue value={page.metadata} />}{rows.map((row, index) => <article key={index}><JsonValue value={row} /></article>)}</div>;
  }
  return <div className="execution-json-result"><span>{t('flowStructuredResponse')}</span><JsonValue value={{ metadata: page.metadata, items: page.items }} /></div>;
}

function CellValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground">{String(value)}</span>;
  if (typeof value === 'object') return <code>{JSON.stringify(value)}</code>;
  return <>{String(value)}</>;
}

function formatLogTimestamp(value: unknown) {
  const raw = String(value || '');
  if (/^\d{16,}$/.test(raw)) {
    try {
      return new Date(Number(BigInt(raw) / 1_000_000n)).toISOString();
    } catch {
      return raw;
    }
  }
  return raw;
}
