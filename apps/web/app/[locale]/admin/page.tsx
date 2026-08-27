'use client';

import { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, CirclePause, CirclePlay, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { createWorkspace, fetchWorkspaces, pauseIngestion, resumeIngestion, startIngestion } from '@/lib/api';
import { Link } from '@/lib/navigation';
import type { Workspace } from '@/lib/types';

export default function WorkspacesPage() {
  const [rows, setRows] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [topic, setTopic] = useState('');
  const [position, setPosition] = useState<'earliest' | 'latest'>('latest');
  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await fetchWorkspaces()); setError(''); }
    catch (cause) { setError(String(cause)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function create() {
    try {
      const row = await createWorkspace({ name, ingestion_topic: topic });
      setRows((current) => [...current, row]);
      setOpen(false); setName(''); setTopic('');
      toast.success('Workspace created');
    } catch (cause) { setError(String(cause)); }
  }

  async function transition(row: Workspace) {
    try {
      const updated = row.ingestion_state === 'draft'
        ? await startIngestion(row.id, position)
        : row.ingestion_state === 'active'
          ? await pauseIngestion(row.id)
          : await resumeIngestion(row.id);
      setRows((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (cause) { setError(String(cause)); }
  }

  return <main className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="eyebrow">CONTROL PLANE</p><h1 className="page-title">Workspaces</h1><p className="page-subtitle">Ingestion ownership, model policy, sources, and evidence access.</p></div>
      <div className="flex gap-2"><Button size="icon" variant="outline" aria-label="Refresh" onClick={() => void load()}><RefreshCw size={16} /></Button><Button variant="primary" onClick={() => setOpen(true)}><Plus size={16} />New workspace</Button></div>
    </header>
    {error && <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</div>}
    <div className="operational-table">
      <div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Kafka topic</th><th>Policy</th><th>Ingestion</th><th>Updated</th><th /></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}>
          <td><Link href={`/admin/workspaces/${row.id}`} className="font-medium hover:text-link">{row.name}</Link></td>
          <td className="mono text-xs">{row.ingestion_topic}</td>
          <td>{row.model_policy_revision_id ? `Revision ${row.model_policy_revision_id}` : <span className="text-warning">Not published</span>}</td>
          <td><span className={`table-status table-status-${row.ingestion_state === 'active' ? 'success' : row.ingestion_state === 'paused' ? 'warning' : 'neutral'}`}><i />{row.ingestion_state}</span></td>
          <td className="text-xs text-muted-foreground">{new Date(row.updated_at).toLocaleString()}</td>
          <td><div className="flex justify-end gap-1"><Button size="icon" variant="ghost" aria-label={`${row.ingestion_state} ingestion`} onClick={() => void transition(row)}>{row.ingestion_state === 'active' ? <CirclePause size={16} /> : <CirclePlay size={16} />}</Button><Button size="icon" variant="ghost" asChild><Link href={`/admin/workspaces/${row.id}`} aria-label="Open workspace"><ArrowUpRight size={16} /></Link></Button></div></td>
        </tr>)}</tbody></table></div>
      {!loading && rows.length === 0 && <p className="p-8 text-center text-muted-foreground">No workspaces.</p>}
      {loading && <p className="p-8 text-center text-muted-foreground">Loading...</p>}
    </div>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>New workspace</DialogTitle></DialogHeader><div className="space-y-4"><label className="field"><span className="field-label">Name</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span className="field-label">Kafka topic</span><Input className="mono" value={topic} onChange={(event) => setTopic(event.target.value)} /></label><label className="field"><span className="field-label">Initial position</span><Select value={position} onChange={(event) => setPosition(event.target.value as 'earliest' | 'latest')}><option value="latest">Latest</option><option value="earliest">Earliest</option></Select></label></div><DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" disabled={!name.trim() || !topic.trim()} onClick={() => void create()}>Create</Button></DialogFooter></DialogContent></Dialog>
  </main>;
}
