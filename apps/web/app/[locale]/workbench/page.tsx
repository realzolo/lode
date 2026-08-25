'use client';

import { useEffect, useMemo, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { IconArrowUpRight, IconSearch } from '@/components/icons';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { createInvestigation, fetchApplications, fetchInvestigations, type InvestigationSummary } from '@/lib/api';
import { Link, useRouter } from '@/lib/navigation';

function statusVariant(item: InvestigationSummary) {
  if (item.status === 'completed' && (item.review_required || item.result_state !== 'confirmed')) return 'warning';
  if (item.status === 'completed') return 'success';
  if (item.status === 'failed') return 'danger';
  if (item.status === 'running') return 'warning';
  return 'accent';
}

export default function InvestigationsPage() {
  const router = useRouter();
  const [investigations, setInvestigations] = useState<InvestigationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [level, setLevel] = useState('all');
  const [application, setApplication] = useState('all');
  const [reload, setReload] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchInvestigations().then((items) => active && setInvestigations(items)).catch((cause) => active && setError(String(cause))).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [reload]);

  const applications = useMemo(() => [...new Map(investigations.map((item) => [item.application_id, item.application_name])).entries()], [investigations]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return investigations.filter((item) => `${item.id} ${item.title} ${item.application_name}`.toLowerCase().includes(needle)
      && (status === 'all' || item.status === status) && (level === 'all' || item.level === level)
      && (application === 'all' || String(item.application_id) === application));
  }, [application, investigations, level, query, status]);
  const filteredActive = Boolean(query || status !== 'all' || level !== 'all' || application !== 'all');

  return <div className="dashboard-page">
    <header className="dashboard-page-header"><div><p className="eyebrow">INVESTIGATIONS</p><h1 className="page-title">错误调查</h1></div><Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}><Plus size={15} />新建调查</Button></header>
    <div className="dashboard-filterbar" aria-label="调查筛选">
      <label className="dashboard-search"><IconSearch size={16} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索事故、应用或调查 ID" aria-label="搜索调查" /></label>
      <Select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="状态" className="dashboard-filter-select"><option value="all">所有状态</option><option value="queued">排队中</option><option value="running">调查中</option><option value="completed">已完成</option><option value="failed">失败</option></Select>
      <Select value={level} onChange={(event) => setLevel(event.target.value)} aria-label="严重级别" className="dashboard-filter-select dashboard-filter-select-sm"><option value="all">所有级别</option><option value="CRITICAL">严重</option><option value="WARNING">警告</option></Select>
      <Select value={application} onChange={(event) => setApplication(event.target.value)} aria-label="应用" className="dashboard-filter-select"><option value="all">所有应用</option>{applications.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</Select>
      {filteredActive && <Button variant="ghost" className="h-9 px-2" onClick={() => { setQuery(''); setStatus('all'); setLevel('all'); setApplication('all'); }}>清除筛选</Button>}
    </div>
    {loading && <div className="analysis-record-list analysis-record-loading" aria-busy="true">{Array.from({ length: 6 }).map((_, index) => <div className="analysis-record" key={index}><Skeleton className="h-4 w-44" /><Skeleton className="h-3.5 w-24" /><Skeleton className="h-3.5 w-20" /><Skeleton className="h-3.5 w-28" /><Skeleton className="h-3.5 w-32" /></div>)}</div>}
    {error && <div className="dashboard-error"><p className="muted" style={{ color: 'var(--danger)' }}>{error}</p><Button variant="outline" size="sm" onClick={() => setReload((value) => value + 1)}>重试</Button></div>}
    {!loading && !error && !investigations.length && <p className="muted dashboard-empty">尚无调查记录。</p>}
    {!loading && !error && investigations.length > 0 && !filtered.length && <p className="muted dashboard-empty">没有匹配的调查。</p>}
    {!loading && !error && filtered.length > 0 && <div className="analysis-record-list" role="list" aria-label="调查列表">{filtered.map((item) => <div key={item.id} role="listitem"><Link className="analysis-record" href={`/workbench/investigation/${item.id}`} aria-label={`查看调查 ${item.title || item.id}`}><span className="analysis-record-title">{item.title || item.id}</span><span className={`table-status table-status-${statusVariant(item)}`}><i />{item.result_state === 'hypothesis' ? '待验证假设' : item.result_state}</span><span className={`analysis-environment analysis-environment-${item.level === 'CRITICAL' ? 'critical' : 'warning'}`}>{item.level}</span><span className="analysis-record-application">{item.application_name}</span><span className="analysis-record-key mono">{item.id}</span><IconArrowUpRight className="analysis-record-arrow" size={15} /></Link></div>)}</div>}
    {createOpen && <CreateInvestigationDialog onClose={() => setCreateOpen(false)} onCreated={(id) => router.push(`/workbench/investigation/${id}`)} />}
  </div>;
}

function CreateInvestigationDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [applications, setApplications] = useState<{ id: string; name: string; myPerm?: string | null }[]>([]);
  const [applicationId, setApplicationId] = useState('');
  const [title, setTitle] = useState('');
  const [name, setName] = useState('Error');
  const [message, setMessage] = useState('');
  const [stack, setStack] = useState('');
  const [deployment, setDeployment] = useState('');
  const [trace, setTrace] = useState('');
  const [severity, setSeverity] = useState<'CRITICAL' | 'WARNING'>('WARNING');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { fetchApplications().then((rows) => { const allowed = rows.filter((row) => ['analyze', 'admin'].includes(row.myPerm || '') || row.myPerm === null); setApplications(allowed); setApplicationId(String(allowed[0]?.id || '')); }).catch((cause) => setError(String(cause))); }, []);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await createInvestigation({ application_id: Number(applicationId), title, severity, occurred_at: new Date().toISOString(), error: { name, message, stack: stack || undefined, properties: {} }, deployment_sha: deployment || undefined, trace_id: trace || undefined });
      onCreated(result.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); setBusy(false); }
  }
  return <div className="audit-backdrop manual-intake-backdrop" onMouseDown={onClose}><form className="manual-intake" onSubmit={(event) => void submit(event)} onMouseDown={(event) => event.stopPropagation()}><header><div><span>手工入口</span><h2>新建错误调查</h2></div><Button type="button" size="icon" variant="ghost" onClick={onClose} aria-label="关闭"><X size={16} /></Button></header><label>应用<Select value={applicationId} onChange={(event) => setApplicationId(event.target.value)}><option value="">选择应用</option>{applications.map((app) => <option key={app.id} value={app.id}>{app.name}</option>)}</Select></label><div className="manual-intake-row"><label>严重级别<Select value={severity} onChange={(event) => setSeverity(event.target.value as 'CRITICAL' | 'WARNING')}><option value="WARNING">WARNING</option><option value="CRITICAL">CRITICAL</option></Select></label><label>错误类型<Input required value={name} onChange={(event) => setName(event.target.value)} /></label></div><label>标题<Input required value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>错误消息<textarea required value={message} onChange={(event) => setMessage(event.target.value)} /></label><label>完整堆栈<textarea className="mono" value={stack} onChange={(event) => setStack(event.target.value)} /></label><div className="manual-intake-row"><label>部署 revision<Input className="mono" value={deployment} onChange={(event) => setDeployment(event.target.value)} /></label><label>Trace ID<Input className="mono" value={trace} onChange={(event) => setTrace(event.target.value)} /></label></div>{error && <p className="manual-intake-error">{error}</p>}<footer><Button type="button" variant="outline" onClick={onClose}>取消</Button><Button type="submit" variant="primary" disabled={busy || !applicationId}>{busy ? '提交中...' : '开始调查'}</Button></footer></form></div>;
}
