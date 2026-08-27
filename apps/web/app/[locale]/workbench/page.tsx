'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, Plus, RefreshCw, Search, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { createInvestigation, fetchInvestigations, fetchWorkspaces } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';
import type { InvestigationSummary, Workspace } from '@/lib/types';

export default function InvestigationsPage() {
  const router = useRouter();
  const [rows, setRows] = useState<InvestigationSummary[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try { const [items, scopes] = await Promise.all([fetchInvestigations(), fetchWorkspaces()]); setRows(items); setWorkspaces(scopes); setError(''); }
    catch (cause) { setError(String(cause)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const names = useMemo(() => new Map(workspaces.map((row) => [row.id, row.name])), [workspaces]);
  const visible = rows.filter((row) => (status === 'all' || row.status === status) && `${row.public_id} ${names.get(row.workspace_id) || ''}`.toLowerCase().includes(query.toLowerCase()));
  return <main className="space-y-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow">INVESTIGATIONS</p><h1 className="page-title">Incident analysis</h1><p className="page-subtitle">Canonical state, evidence, model routing, and source authority.</p></div><div className="flex gap-2"><Button size="icon" variant="outline" aria-label="Refresh" onClick={() => void load()}><RefreshCw size={16} /></Button><Button variant="primary" onClick={() => setOpen(true)}><Plus size={16} />New investigation</Button></div></header>
    <div className="flex flex-wrap gap-2 border-y py-3"><label className="relative min-w-64 flex-1"><Search className="absolute left-3 top-2.5 text-muted-foreground" size={16} /><Input className="pl-9" placeholder="Search public ID or workspace" value={query} onChange={(e) => setQuery(e.target.value)} /></label><Select className="w-40" value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">All states</option><option value="queued">Queued</option><option value="running">Running</option><option value="completed">Completed</option><option value="failed">Failed</option></Select>{(query || status !== 'all') && <Button size="icon" variant="ghost" aria-label="Clear filters" onClick={() => { setQuery(''); setStatus('all'); }}><X size={16} /></Button>}</div>
    {error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    <div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>Investigation</th><th>Workspace</th><th>Status</th><th>Result</th><th>Created</th><th /></tr></thead><tbody>{visible.map((row) => <tr key={row.id}><td className="mono text-xs">{row.public_id}</td><td>{names.get(row.workspace_id) || row.workspace_id}</td><td><span className={`table-status table-status-${row.status === 'completed' ? 'success' : row.status === 'failed' ? 'danger' : row.status === 'running' ? 'warning' : 'neutral'}`}><i />{row.status}</span></td><td>{row.result_state}</td><td className="text-xs text-muted-foreground">{new Date(row.created_at).toLocaleString()}</td><td><Button size="icon" variant="ghost" asChild><Link href={`/workbench/investigation/${row.public_id}`} aria-label="Open investigation"><ArrowUpRight size={16} /></Link></Button></td></tr>)}</tbody></table></div>{visible.length === 0 && <p className="p-8 text-center text-muted-foreground">No matching investigations.</p>}</div>
    <CreateDialog open={open} onOpenChange={setOpen} workspaces={workspaces} onCreated={(id) => router.push(`/workbench/investigation/${id}`)} />
  </main>;
}

function CreateDialog({ open, onOpenChange, workspaces, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaces: Workspace[]; onCreated: (id: string) => void }) {
  const [workspaceId, setWorkspaceId] = useState(''); const [event, setEvent] = useState('manual.error'); const [severity, setSeverity] = useState<'CRITICAL' | 'WARNING'>('WARNING'); const [type, setType] = useState('RuntimeError'); const [message, setMessage] = useState(''); const [stack, setStack] = useState(''); const [trace, setTrace] = useState(''); const [error, setError] = useState('');
  async function create() { try { const result = await createInvestigation({ workspace_id: Number(workspaceId), occurred_at: new Date().toISOString(), severity, event, trace_id: trace || null, source_revision: null, error: { type, message, stack, cause: null }, attachments: [] }); onOpenChange(false); onCreated(result.id); } catch (cause) { setError(String(cause)); } }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>New investigation</DialogTitle></DialogHeader><div className="grid gap-3 sm:grid-cols-2"><Select value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)}><option value="">Workspace</option>{workspaces.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</Select><Select value={severity} onChange={(e) => setSeverity(e.target.value as 'CRITICAL' | 'WARNING')}><option value="WARNING">WARNING</option><option value="CRITICAL">CRITICAL</option></Select><Input placeholder="Event key" value={event} onChange={(e) => setEvent(e.target.value)} /><Input placeholder="Error type" value={type} onChange={(e) => setType(e.target.value)} /><Input className="sm:col-span-2" placeholder="Trace ID (optional)" value={trace} onChange={(e) => setTrace(e.target.value)} /><Textarea className="sm:col-span-2" placeholder="Error message" value={message} onChange={(e) => setMessage(e.target.value)} /><Textarea className="mono min-h-32 sm:col-span-2" placeholder="Stack trace" value={stack} onChange={(e) => setStack(e.target.value)} /></div>{error && <p className="text-sm text-destructive">{error}</p>}<DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!workspaceId || !event || !type} onClick={() => void create()}>Start</Button></DialogFooter></DialogContent></Dialog>;
}
