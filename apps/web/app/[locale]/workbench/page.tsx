'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, Plus, RefreshCw, Search, X } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ListSkeleton } from '@/components/ui/list-skeleton';
import { apiErrorMessage, createIncident, fetchIncidents, fetchWorkspaces } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';
import type { IncidentState, IncidentSummary, Workspace } from '@/lib/types';

function stateLabel(state: IncidentState, t: ReturnType<typeof useTranslations>) {
  return t({
    open: 'stateOpen',
    acknowledged: 'stateAcknowledged',
    mitigated: 'stateMitigated',
    resolved: 'stateResolved',
    closed: 'stateClosed',
  }[state]);
}

function severityLabel(severity: IncidentSummary['severity'], t: ReturnType<typeof useTranslations>) {
  return t(severity === 'CRITICAL' ? 'severityCritical' : 'severityWarning');
}

export default function IncidentsPage() {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const locale = useLocale();
  const router = useRouter();
  const [rows, setRows] = useState<IncidentSummary[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [query, setQuery] = useState('');
  const [state, setState] = useState('all');
  const [nextAfterId, setNextAfterId] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (append = false, afterId?: number) => {
    if (!append) setRefreshing(true);
    try {
      const [page, scopes] = await Promise.all([
        fetchIncidents({ state, q: query, afterId }),
        append ? Promise.resolve(null) : fetchWorkspaces(),
      ]);
      setRows((current) => append ? [...current, ...page.items] : page.items);
      if (scopes) setWorkspaces(scopes);
      setNextAfterId(page.next_after_id);
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [query, state, tc]);

  useEffect(() => { void load(); }, [load]);

  const names = useMemo(() => new Map(workspaces.map((row) => [row.id, row.name])), [workspaces]);
  const dateLocale = locale === 'zh' ? 'zh-CN' : 'en-US';

  return <main className="dashboard-page space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="eyebrow">{t('eyebrow')}</p>
        <h1 className="page-title">{t('title')}</h1>
        <p className="page-subtitle">{t('subtitle')}</p>
      </div>
      <div className="flex gap-2">
        <Button size="icon" variant="outline" loading={refreshing} aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
        <Button variant="primary" onClick={() => setOpen(true)}><Plus size={16} />{t('new')}</Button>
      </div>
    </header>
    <div className="flex flex-wrap gap-2 border-y py-3">
      <label className="relative min-w-64 flex-1">
        <Search className="absolute left-3 top-2.5 text-muted-foreground" size={16} />
        <Input className="pl-9" placeholder={t('search')} value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      <Select className="w-44" value={state} onChange={(event) => setState(event.target.value)} aria-label={t('state')}>
        <option value="all">{t('allStates')}</option>
        {(['open', 'acknowledged', 'mitigated', 'resolved', 'closed'] as IncidentState[]).map((value) => <option key={value} value={value}>{stateLabel(value, t)}</option>)}
      </Select>
      {(query || state !== 'all') && <Button size="icon" variant="ghost" aria-label={tc('clearFilters')} title={tc('clearFilters')} onClick={() => { setQuery(''); setState('all'); }}><X size={16} /></Button>}
    </div>
    {error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    {loading ? <ListSkeleton rows={6} columns={6} /> : <div className="operational-table">
      <div className="table-wrap"><table className="table"><thead><tr><th>{t('incident')}</th><th>{t('workspace')}</th><th>{t('state')}</th><th>{t('occurrences')}</th><th>{t('latestOccurrence')}</th><th /></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}>
          <td><p className="font-medium">{row.event}</p><p className="mono mt-1 text-xs text-muted-foreground">{row.component} · {row.environment} · {row.dedup_key}</p></td>
          <td>{names.get(row.workspace_id) || row.workspace_id}</td>
          <td><span className={`table-status table-status-${row.state === 'closed' ? 'neutral' : row.state === 'resolved' ? 'success' : row.state === 'mitigated' ? 'warning' : 'danger'}`}><i />{stateLabel(row.state, t)}</span><p className="mt-1 text-xs text-muted-foreground">{severityLabel(row.severity, t)}</p></td>
          <td>{row.occurrence_count}</td>
          <td className="text-xs text-muted-foreground">{new Date(row.last_occurred_at).toLocaleString(dateLocale)}</td>
          <td><Button size="icon" variant="ghost" asChild><Link href={`/workbench/incident/${row.id}`} aria-label={tc('open')} title={tc('open')}><ArrowUpRight size={16} /></Link></Button></td>
        </tr>)}</tbody>
      </table></div>
      {rows.length === 0 && <p className="p-8 text-center text-muted-foreground">{t('noMatching')}</p>}
    </div>}
    {nextAfterId !== null && <div className="flex justify-center"><Button variant="outline" onClick={() => void load(true, nextAfterId)}>{t('loadMore')}</Button></div>}
    <CreateIncidentDialog open={open} onOpenChange={setOpen} workspaces={workspaces} onCreated={(id) => router.push(`/workbench/incident/${id}`)} />
  </main>;
}

function CreateIncidentDialog({ open, onOpenChange, workspaces, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaces: Workspace[]; onCreated: (id: number) => void }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const [workspaceId, setWorkspaceId] = useState('');
  const [dedupKey, setDedupKey] = useState('manual.error');
  const [event, setEvent] = useState('manual.error');
  const [component, setComponent] = useState('manual');
  const [environment, setEnvironment] = useState('production');
  const [severity, setSeverity] = useState<'CRITICAL' | 'WARNING'>('WARNING');
  const [type, setType] = useState('RuntimeError');
  const [message, setMessage] = useState('');
  const [stack, setStack] = useState('');
  const [trace, setTrace] = useState('');
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);

  async function create() {
    setCreating(true);
    try {
      const result = await createIncident({
        workspace_id: Number(workspaceId), dedup_key: dedupKey, event_kind: 'firing', occurred_at: new Date().toISOString(), severity, event, component, environment,
        trace_id: trace || null, source_revision: null, error: { type, message, stack, cause: null }, attachments: [],
      });
      onOpenChange(false);
      onCreated(result.incident_id);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setCreating(false);
    }
  }

  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('newTitle')}</DialogTitle></DialogHeader>
    <div className="grid gap-3 sm:grid-cols-2">
      <Select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}><option value="">{t('workspacePlaceholder')}</option>{workspaces.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</Select>
      <Select value={severity} onChange={(event) => setSeverity(event.target.value as 'CRITICAL' | 'WARNING')}><option value="WARNING">{severityLabel('WARNING', t)}</option><option value="CRITICAL">{severityLabel('CRITICAL', t)}</option></Select>
      <Input placeholder={t('dedupKey')} value={dedupKey} onChange={(event) => setDedupKey(event.target.value)} />
      <Input placeholder={t('event')} value={event} onChange={(event) => setEvent(event.target.value)} />
      <Input placeholder={t('component')} value={component} onChange={(event) => setComponent(event.target.value)} />
      <Input placeholder={t('environment')} value={environment} onChange={(event) => setEnvironment(event.target.value)} />
      <Input placeholder={t('errorType')} value={type} onChange={(event) => setType(event.target.value)} />
      <Input placeholder={t('traceId')} value={trace} onChange={(event) => setTrace(event.target.value)} />
      <Textarea className="sm:col-span-2" placeholder={t('errorMessage')} value={message} onChange={(event) => setMessage(event.target.value)} />
      <Textarea className="mono min-h-32 sm:col-span-2" placeholder={t('stackTrace')} value={stack} onChange={(event) => setStack(event.target.value)} />
    </div>
    {error && <p className="text-sm text-destructive">{error}</p>}
    <DialogFooter><Button variant="outline" disabled={creating} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={creating} loadingText={tc('loading')} disabled={!workspaceId || !dedupKey || !event || !component || !environment || !type} onClick={() => void create()}>{tc('start')}</Button></DialogFooter>
  </DialogContent></Dialog>;
}
