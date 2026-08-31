'use client';

import { AlertTriangle } from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { CausalGraph, InvestigationReportView } from '@/lib/types';

type EvidenceHandler = (id: number) => void;

export function IncidentReportPanel({ report, onEvidence }: { report: InvestigationReportView; onEvidence: EvidenceHandler }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  return <div className="space-y-6 border-y py-5">
    <div><h3 className="text-lg font-semibold">{report.headline}</h3><p className="mt-2 max-w-4xl text-sm leading-6">{report.executive_summary}</p></div>
    {report.impact_scope.length > 0 && <EvidenceStatementList title={t('impactScope')} items={report.impact_scope} onEvidence={onEvidence} />}
    <CausalGraphPanel graph={report.causal_graph} onEvidence={onEvidence} />
    {report.participants.length > 0 && <section><h3 className="font-semibold">{t('participants')}</h3><div className="mt-2 divide-y border-y">{report.participants.map((item) => <div key={item.entity_ref} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"><span>{item.display_name} · {item.identity_status}</span><EvidenceRefs values={item.evidence_refs} onSelect={onEvidence} /></div>)}</div></section>}
    {report.timeline_summary.length > 0 && <section><h3 className="font-semibold">{t('reportTimeline')}</h3><div className="mt-2 divide-y border-y">{report.timeline_summary.map((item) => <div key={item.event_ref} className="py-2 text-sm"><span className="text-xs text-muted-foreground">{new Date(item.occurred_at).toLocaleString()}</span><p>{item.summary}</p><EvidenceRefs values={item.evidence_refs} onSelect={onEvidence} /></div>)}</div></section>}
    {report.counter_evidence.length > 0 && <EvidenceStatementList title={t('counterEvidence')} items={report.counter_evidence} onEvidence={onEvidence} />}
    {report.evidence_gaps.length > 0 && <section><h3 className="font-semibold">{t('evidenceGaps')}</h3><div className="mt-2 divide-y border-y">{report.evidence_gaps.map((gap) => <div key={gap.description} className="flex gap-2 py-3 text-sm"><AlertTriangle className="mt-0.5 shrink-0 text-warning" size={15} /><div><strong>{gap.description}</strong><p className="mt-1 text-muted-foreground">{gap.consequence}</p><p className="mt-1">{gap.required_evidence}</p></div></div>)}</div></section>}
    {report.action_recommendations.length > 0 && <section><h3 className="font-semibold">{t('recommendedNextStep')}</h3><div className="mt-2 divide-y border-y">{report.action_recommendations.map((item) => <article key={`${item.action_type}:${item.title}`} className="py-3"><div className="flex justify-between gap-2"><strong>{item.title}</strong><span className="text-xs text-muted-foreground">{item.priority}</span></div><p className="mt-1 text-sm">{item.rationale}</p><p className="mt-1 text-sm text-muted-foreground">{item.validation}</p><EvidenceRefs values={item.evidence_refs} onSelect={onEvidence} /></article>)}</div></section>}
    {report.code_findings.length > 0 && <section><h3 className="font-semibold">{t('codeFindings')}</h3><div className="mt-2 divide-y border-y">{report.code_findings.map((finding) => <article key={finding.id} className="py-3"><strong>{finding.path || tc('empty')}{finding.symbol ? ` · ${finding.symbol}` : ''}</strong><p className="mt-2 text-sm">{finding.faulty_behavior}</p><p className="mt-1 text-sm text-muted-foreground">{finding.why_wrong}</p><EvidenceRefs values={[...finding.incident_evidence_refs, ...finding.supporting_evidence_refs, ...finding.counter_evidence_refs]} onSelect={onEvidence} /></article>)}</div></section>}
    <details className="border-t pt-3"><summary className="cursor-pointer text-sm font-medium">{t('sourceAssessments')}</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{JSON.stringify(report.source_assessments, null, 2)}</pre></details>
    <details className="border-t pt-3"><summary className="cursor-pointer text-sm font-medium">{t('configurationAssessments')}</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{JSON.stringify(report.configuration_assessments, null, 2)}</pre></details>
  </div>;
}

function EvidenceStatementList({ title, items, onEvidence }: { title: string; items: Array<{ text: string; evidence_refs: number[] }>; onEvidence: EvidenceHandler }) {
  return <section><h3 className="font-semibold">{title}</h3><ul className="mt-2 space-y-2 text-sm">{items.map((item) => <li key={item.text} className="border-l-2 border-primary pl-3">{item.text}<EvidenceRefs values={item.evidence_refs} onSelect={onEvidence} /></li>)}</ul></section>;
}

function EvidenceRefs({ values, onSelect }: { values: number[]; onSelect: EvidenceHandler }) {
  const unique = [...new Set(values)];
  return unique.length ? <span className="mt-2 flex flex-wrap gap-x-2 text-xs">{unique.map((id) => <button key={id} type="button" className="font-mono text-primary hover:underline" onClick={() => onSelect(id)}>#{id}</button>)}</span> : null;
}

function CausalGraphPanel({ graph, onEvidence }: { graph: CausalGraph; onEvidence: EvidenceHandler }) {
  const t = useTranslations('workbench');
  return <section><h3 className="font-semibold">{t('causalGraph')}</h3><div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(260px,.65fr)]"><div className="grid gap-2 sm:grid-cols-2">{graph.nodes.map((node) => <article key={node.node_id} id={`causal-${node.node_id}`} className={`border-l-4 p-3 ${node.status === 'confirmed' ? 'border-primary' : node.status === 'refuted' ? 'border-destructive' : 'border-warning'}`}><div className="flex justify-between gap-2"><strong className="text-sm">{t(`causalNodeTypes.${node.node_type}`)}</strong><span className="text-xs text-muted-foreground">{node.status}</span></div><p className="mt-2 text-sm">{node.statement}</p><EvidenceRefs values={node.evidence_refs} onSelect={onEvidence} /></article>)}</div><div className="divide-y border-y">{graph.edges.map((edge) => <div key={edge.edge_id} className="py-3 text-sm"><strong>{edge.source_node_id} → {edge.target_node_id}</strong><p className="mt-1 text-muted-foreground">{t(`causalRelations.${edge.relation}`)} · {edge.statement}</p><EvidenceRefs values={edge.evidence_refs} onSelect={onEvidence} /></div>)}</div></div></section>;
}
