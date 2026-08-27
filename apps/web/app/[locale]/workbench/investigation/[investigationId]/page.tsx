'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Archive, ArrowLeft, GitCommitHorizontal, RefreshCw, RotateCcw, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tabs } from '@/components/ui/tabs';
import { archiveInvestigation, fetchInvestigation, fetchInvestigationAudit, openInvestigationStream, retryInvestigation } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';
import type { InvestigationDetail } from '@/lib/types';

export default function InvestigationPage({ params }: { params: { investigationId: string } }) {
  const router = useRouter();
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [audit, setAudit] = useState<Record<string, Array<Record<string, unknown>>>>({});
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const load = useCallback(async () => {
    try { const [value, auditRows] = await Promise.all([fetchInvestigation(params.investigationId), fetchInvestigationAudit(params.investigationId)]); setDetail(value); setAudit(auditRows); setError(''); }
    catch (cause) { setError(String(cause)); }
  }, [params.investigationId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => openInvestigationStream(params.investigationId, Number(detail?.investigation.event_cursor || 0), () => { if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => void load(), 150); }), [detail?.investigation.event_cursor, load, params.investigationId]);
  if (!detail) return <main className="p-8 text-sm text-muted-foreground">{error || 'Loading investigation...'}</main>;
  const root = detail.investigation;
  const terminal = root.status === 'completed' || root.status === 'failed';
  const report = detail.report;
  const headline = typeof report?.headline === 'string' ? report.headline : 'Analysis in progress';
  const tabs = [
    { value: 'timeline', label: `Timeline (${detail.operations.length})`, content: <Timeline detail={detail} /> },
    { value: 'evidence', label: `Evidence (${detail.evidence.artifacts.length})`, content: <RecordTable rows={detail.evidence.artifacts} empty="No evidence artifacts." /> },
    { value: 'source', label: `Source (${detail.source_assessments.length})`, content: <Source detail={detail} /> },
    { value: 'models', label: `Model routing (${detail.model_routing.length})`, content: <Models detail={detail} /> },
    { value: 'audit', label: 'Execution audit', content: <Audit values={audit} /> },
  ];
  async function retry() { try { const created = await retryInvestigation(root.public_id); router.push(`/workbench/investigation/${created.id}`); } catch (cause) { setError(String(cause)); } }
  async function archive() { try { await archiveInvestigation(root.public_id); await load(); } catch (cause) { setError(String(cause)); } }
  return <main className="space-y-6"><header className="border-b pb-6"><div className="mb-5 flex items-center justify-between"><Button size="sm" variant="ghost" asChild><Link href="/workbench"><ArrowLeft size={15} />Investigations</Link></Button><div className="flex gap-2">{terminal && !root.archived_at && <Button size="sm" variant="outline" onClick={() => void retry()}><RotateCcw size={15} />Retry</Button>}{terminal && !root.archived_at && <Button size="sm" variant="outline" onClick={() => void archive()}><Archive size={15} />Archive</Button>}<Button size="icon" variant="outline" aria-label="Refresh" onClick={() => void load()}><RefreshCw size={16} /></Button></div></div><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="mb-2 flex items-center gap-2"><span className={`table-status table-status-${root.status === 'completed' ? 'success' : root.status === 'failed' ? 'danger' : 'warning'}`}><i />{root.status}</span><span className="text-xs text-muted-foreground">{root.result_state}</span>{root.archived_at && <span className="text-xs text-muted-foreground">Archived</span>}</div><h1 className="max-w-4xl text-2xl font-semibold tracking-normal">{headline}</h1><p className="mt-2 mono text-xs text-muted-foreground">{root.public_id}</p></div><dl className="grid min-w-56 grid-cols-2 gap-x-5 gap-y-2 text-xs"><dt className="text-muted-foreground">Workspace</dt><dd>{root.workspace_id}</dd><dt className="text-muted-foreground">Event</dt><dd>{detail.input?.event}</dd><dt className="text-muted-foreground">Severity</dt><dd>{detail.input?.severity}</dd><dt className="text-muted-foreground">Occurred</dt><dd>{detail.input ? new Date(detail.input.occurred_at).toLocaleString() : 'Unknown'}</dd></dl></div>{error && <p className="mt-4 text-sm text-destructive">{error}</p>}</header>
    {report && <section className="border-b pb-6"><p className="eyebrow">REPORT</p><p className="mt-2 max-w-4xl text-base leading-7">{String(report.summary || '')}</p><div className="mt-4 grid gap-4 md:grid-cols-2"><JsonPanel title="Incident cause" value={report.incident_cause} /><JsonPanel title="Code diagnosis" value={report.code_diagnosis} /></div></section>}
    <Tabs items={tabs} />
  </main>;
}

function Timeline({ detail }: { detail: InvestigationDetail }) {
  return <div className="divide-y border-y">{detail.operations.map((operation, index) => <article key={String(operation.id || index)} className="grid gap-3 py-4 md:grid-cols-[48px_1fr_180px]"><div className="flex h-8 w-8 items-center justify-center rounded-sm border font-mono text-xs">{String(operation.ordinal || index + 1)}</div><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{String(operation.purpose || operation.operation_kind || 'Operation')}</h3><span className="text-xs text-muted-foreground">{String(operation.operation_kind || '')}</span></div><p className="mt-1 text-sm text-muted-foreground">{String(operation.expected_evidence || operation.selection_reason || '')}</p>{operation.failure_code ? <p className="mt-2 text-xs text-destructive">{String(operation.failure_code)}</p> : null}</div><div className="text-xs text-muted-foreground md:text-right"><div>{String(operation.status || '')}</div><div className="mt-1">{operation.finished_at ? new Date(String(operation.finished_at)).toLocaleString() : 'Pending'}</div></div></article>)}{detail.operations.length === 0 && <p className="py-8 text-center text-muted-foreground">Waiting for the first operation.</p>}</div>;
}

function Source({ detail }: { detail: InvestigationDetail }) {
  return <div className="space-y-5"><div className="grid gap-4 md:grid-cols-2"><JsonPanel title="Frozen source revisions" value={detail.source_revisions} icon={<GitCommitHorizontal size={16} />} /><JsonPanel title="Runtime assessments" value={detail.source_assessments} icon={<ShieldCheck size={16} />} /></div><section><h3 className="mb-3 text-sm font-semibold">Code findings</h3><RecordTable rows={detail.code_findings} empty="No code finding was published." /></section></div>;
}

function Models({ detail }: { detail: InvestigationDetail }) {
  return <div className="space-y-5"><section><h3 className="mb-3 text-sm font-semibold">Routing decisions</h3><RecordTable rows={detail.model_routing} empty="No model routing decisions." /></section><section><h3 className="mb-3 text-sm font-semibold">Context revisions</h3><RecordTable rows={detail.context_revisions} empty="No context revisions." /></section></div>;
}

function Audit({ values }: { values: Record<string, Array<Record<string, unknown>>> }) {
  return <div className="space-y-6">{Object.entries(values).map(([name, rows]) => <section key={name}><h3 className="mb-3 text-sm font-semibold">{name.replaceAll('_', ' ')}</h3><RecordTable rows={rows} empty="No records." /></section>)}</div>;
}

function RecordTable({ rows, empty }: { rows: Array<Record<string, unknown>>; empty: string }) {
  if (!rows.length) return <p className="border-y py-8 text-center text-sm text-muted-foreground">{empty}</p>;
  return <div className="space-y-2">{rows.map((row, index) => <details key={String(row.id || index)} className="rounded-md border bg-card"><summary className="cursor-pointer px-4 py-3 text-sm font-medium">#{String(row.id || index + 1)} · {String(row.status || row.role || row.artifact_kind || row.runtime_match_status || 'record')}</summary><pre className="max-h-96 overflow-auto border-t p-4 text-xs">{JSON.stringify(row, null, 2)}</pre></details>)}</div>;
}

function JsonPanel({ title, value, icon }: { title: string; value: unknown; icon?: ReactNode }) {
  return <section className="rounded-md border bg-card p-4"><h3 className="flex items-center gap-2 text-sm font-semibold">{icon}{title}</h3><pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{JSON.stringify(value, null, 2)}</pre></section>;
}
