'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, CloudDownload, Pencil, Plus, RefreshCw } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState, TableEmptyState } from '@/components/ui/empty-state';
import { TableColumns } from '@/components/ui/table';
import {
  apiErrorMessage,
  createProviderAccount,
  discoverProviderModels,
  fetchProviderAccounts,
  fetchProviderModelCatalog,
  refreshProviderModels,
  testProviderAccountModel,
  updateProviderAccount,
} from '@/lib/api';
import type { ProviderAccount, ProviderModelCatalogItem } from '@/lib/types';

type ProviderKind = 'openai' | 'anthropic';
type ModelSource = 'discovered' | 'manual';

const PROTOCOLS: Record<ProviderKind, Array<{ id: string; label: string }>> = {
  openai: [
    { id: 'openai.responses.v1', label: 'OpenAI Responses' },
    { id: 'openai.chat_completions.v1', label: 'OpenAI Chat Completions' },
  ],
  anthropic: [{ id: 'anthropic.messages.v1', label: 'Anthropic Messages' }],
};
const DEFAULT_URL: Record<ProviderKind, string> = {
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
};

function availabilityTone(state: ProviderAccount['models'][number]['availability_state']) {
  return state === 'healthy' ? 'success' : state === 'unavailable' ? 'danger' : 'warning';
}

function accountStateTone(state: ProviderAccount['models'][number]['state']) {
  return state === 'active' ? 'success' : 'neutral';
}

export default function ModelsPage() {
  const t = useTranslations('admin');
  const tc = useTranslations('common');
  const [accounts, setAccounts] = useState<ProviderAccount[]>([]);
  const [editing, setEditing] = useState<ProviderAccount | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyModel, setBusyModel] = useState<number | null>(null);
  const [busyAccount, setBusyAccount] = useState<number | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async (background = false) => {
    background ? setRefreshing(true) : setLoading(true);
    try {
      setAccounts(await fetchProviderAccounts());
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [tc]);
  useEffect(() => { void load(); }, [load]);

  async function refreshModels(account: ProviderAccount) {
    setBusyAccount(account.id);
    try {
      const result = await refreshProviderModels(account.id);
      await load(true);
      toast.success(t('modelsDiscovered', { count: result.available_model_ids.length }));
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setBusyAccount(null);
    }
  }

  async function probe(accountId: number, modelId: number) {
    setBusyModel(modelId);
    try {
      await testProviderAccountModel(accountId, modelId);
      await load(true);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setBusyModel(null);
    }
  }

  return (
    <main className="dashboard-page space-y-6">
      <header className="dashboard-page-header">
        <div><h1 className="page-title">{t('providerAccounts')}</h1><p className="page-subtitle">{t('modelsSubtitle')}</p></div>
        <div className="flex gap-2">
          <Button size="icon" variant="outline" loading={refreshing} title={tc('refresh')} aria-label={tc('refresh')} onClick={() => void load(true)}><RefreshCw size={16} /></Button>
          <Button size="sm" variant="primary" onClick={() => { setEditing(null); setOpen(true); }}><Plus size={16} />{t('addAccount')}</Button>
        </div>
      </header>
      {error && accounts.length > 0 ? <div role="alert" className="dashboard-feedback justify-between gap-3"><span>{error}</span><Button size="sm" variant="outline" onClick={() => void load()}>{tc('retry')}</Button></div> : null}
      {loading ? <AccountSkeleton /> : error && accounts.length === 0 ? <TableEmptyState title={tc('requestFailed')} action={<Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw size={15} />{tc('retry')}</Button>} /> : accounts.length === 0 ? (
        <TableEmptyState
          title={t('noAccounts')}
          description={t('noAccountsDescription')}
          action={<Button size="sm" variant="primary" onClick={() => setOpen(true)}><Plus size={16} />{t('addAccount')}</Button>}
        />
      ) : (
        <section className="dashboard-record-list">
          {accounts.map((account) => (
            <article key={account.id} className="dashboard-record">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1"><div className="flex min-w-0 items-center gap-2"><h2 className="truncate font-semibold">{account.name}</h2><span className="status-badge shrink-0">{account.provider_kind === 'openai' ? 'OpenAI' : 'Anthropic'}</span></div><p className="mono mt-1 break-words text-xs text-muted-foreground">{account.protocol_id} · {account.base_url}</p></div>
                <div className="flex shrink-0 gap-1"><Button size="icon" variant="ghost" loading={busyAccount === account.id} title={t('syncModels')} aria-label={t('syncModels')} onClick={() => void refreshModels(account)}><CloudDownload size={16} /></Button><Button size="icon" variant="ghost" title={t('editAccount')} aria-label={t('editAccount')} onClick={() => { setEditing(account); setOpen(true); }}><Pencil size={16} /></Button></div>
              </div>
              {account.models.length === 0 ? <EmptyState title={t('noModels')} /> : <div className="operational-table">
                <div className="table-wrap"><table className="table"><TableColumns widths={[42, 18, 18, 22]} trailingWidth={64} /><thead><tr><th>{t('providerModel')}</th><th>{t('source')}</th><th>{t('availability')}</th><th>{t('state')}</th><th><span className="sr-only">{tc('actions')}</span></th></tr></thead>
                  <tbody>{account.models.map((model) => <tr key={model.id}><td><span className="font-medium">{model.display_name}</span><span className="ml-2 mono text-xs text-muted-foreground">{model.provider_model_id}</span></td><td>{t(`modelSource.${model.discovery_state}`)}</td><td><span className={`table-status table-status-${availabilityTone(model.availability_state)}`}><i aria-hidden="true" />{t(`availabilityState.${model.availability_state}`)}</span></td><td><span className={`table-status table-status-${accountStateTone(model.state)}`}><i aria-hidden="true" />{t(`accountState.${model.state}`)}</span></td><td className="text-right"><Button size="icon" variant="ghost" loading={busyModel === model.id} disabled={model.state !== 'active'} title={t('probeModel')} aria-label={t('probeModel')} onClick={() => void probe(account.id, model.id)}><Activity size={16} /></Button></td></tr>)}</tbody>
                </table></div>
              </div>}
            </article>
          ))}
        </section>
      )}
      <AccountDialog open={open} account={editing} onOpenChange={setOpen} onSaved={() => load(true)} />
    </main>
  );
}

function AccountSkeleton() {
  return <section className="list-skeleton" aria-busy="true">{[0, 1].map((row) => <div key={row} className="space-y-4 border-b border-[var(--dashboard-border)] p-4 last:border-b-0"><div className="flex justify-between"><div className="space-y-2"><Skeleton className="h-4 w-40" /><Skeleton className="h-3 w-72" /></div><Skeleton className="h-8 w-20" /></div><Skeleton className="h-24 w-full" /></div>)}</section>;
}

function AccountDialog({ open, account, onOpenChange, onSaved }: { open: boolean; account: ProviderAccount | null; onOpenChange: (value: boolean) => void; onSaved: () => Promise<void> }) {
  const tc = useTranslations('common');
  const t = useTranslations('admin');
  const [name, setName] = useState('');
  const [providerKind, setProviderKind] = useState<ProviderKind>('openai');
  const [protocolId, setProtocolId] = useState(PROTOCOLS.openai[0].id);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_URL.openai);
  const [apiKey, setApiKey] = useState('');
  const [catalog, setCatalog] = useState<ProviderModelCatalogItem[]>([]);
  const [selected, setSelected] = useState<Record<string, ModelSource>>({});
  const [manualId, setManualId] = useState('');
  const [unsupportedCount, setUnsupportedCount] = useState(0);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    const kind = account?.provider_kind ?? 'openai';
    const protocol = account?.protocol_id ?? PROTOCOLS[kind][0].id;
    setName(account?.name ?? ''); setProviderKind(kind); setProtocolId(protocol);
    setBaseUrl(account?.base_url ?? DEFAULT_URL[kind]); setApiKey(''); setManualId(''); setUnsupportedCount(0); setError('');
    setSelected(Object.fromEntries(account?.models.filter((model) => model.state === 'active').map((model) => [model.provider_model_id, model.discovery_state === 'manual' ? 'manual' : 'discovered']) ?? []));
  }, [account, open]);

  useEffect(() => {
    if (!open) return;
    setLoadingCatalog(true);
    void fetchProviderModelCatalog(providerKind, protocolId).then(setCatalog).catch((cause) => setError(apiErrorMessage(cause, tc('requestFailed')))).finally(() => setLoadingCatalog(false));
  }, [open, protocolId, providerKind]);

  const catalogIds = useMemo(() => new Set(catalog.map((model) => model.provider_model_id)), [catalog]);
  function changeProvider(kind: ProviderKind) {
    setProviderKind(kind); setProtocolId(PROTOCOLS[kind][0].id); setBaseUrl(DEFAULT_URL[kind]); setSelected({}); setUnsupportedCount(0); setError('');
  }
  async function discover() {
    setDiscovering(true); setError('');
    try {
      const result = account && !apiKey
        ? await refreshProviderModels(account.id)
        : await discoverProviderModels({ provider_kind: providerKind, protocol_id: protocolId, base_url: baseUrl, api_key: apiKey });
      setUnsupportedCount(result.unsupported_model_ids.length);
      setSelected((current) => ({ ...current, ...Object.fromEntries(result.available_model_ids.map((id) => [id, 'discovered' as const])) }));
    } catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); } finally { setDiscovering(false); }
  }
  function addManual() {
    const value = manualId.trim();
    if (!catalogIds.has(value)) { setError(t('unsupportedManualModel')); return; }
    setSelected((current) => ({ ...current, [value]: 'manual' })); setManualId(''); setError('');
  }
  async function save() {
    setSaving(true); setError('');
    const input = { name: name.trim(), provider_kind: providerKind, protocol_id: protocolId, base_url: baseUrl.trim(), ...(apiKey ? { api_key: apiKey } : {}), models: Object.entries(selected).map(([provider_model_id, source]) => ({ provider_model_id, source })) };
    try { account ? await updateProviderAccount(account.id, input) : await createProviderAccount(input); onOpenChange(false); await onSaved(); }
    catch (cause) { setError(apiErrorMessage(cause, tc('requestFailed'))); } finally { setSaving(false); }
  }

  const canDiscover = Boolean(baseUrl && (account || apiKey));
  const canSave = Boolean(name.trim() && baseUrl.trim() && Object.keys(selected).length && (account || apiKey));
  return <Dialog open={open} onOpenChange={(value) => !saving && onOpenChange(value)}><DialogContent variant="drawer" className="max-w-2xl overflow-hidden p-0"><DialogHeader className="border-b px-6 py-5"><DialogTitle>{account ? t('editAccount') : t('addAccount')}</DialogTitle></DialogHeader><div className="h-[calc(100dvh-145px)] space-y-5 overflow-y-auto px-6 py-5">
    {error ? <p role="alert" className="dashboard-feedback">{error}</p> : null}
    <label className="field"><span className="field-label">{t('accountName')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label>
    <div className="grid gap-4 sm:grid-cols-2"><label className="field"><span className="field-label">{t('provider')}</span><Select value={providerKind} onChange={(event) => changeProvider(event.target.value as ProviderKind)}><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></Select></label><label className="field"><span className="field-label">{t('messageFormat')}</span><Select value={protocolId} onChange={(event) => { setProtocolId(event.target.value); setSelected({}); }}>{PROTOCOLS[providerKind].map((protocol) => <option key={protocol.id} value={protocol.id}>{protocol.label}</option>)}</Select></label></div>
    <label className="field"><span className="field-label">{t('httpsBaseUrl')}</span><Input className="mono" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
    <label className="field"><span className="field-label">{t('apiKey')}</span><Input type="password" autoComplete="off" placeholder={account ? t('apiKeyOptional') : t('apiKey')} value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
    <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-medium">{t('accountModels')}</h3><p className="text-xs text-muted-foreground">{t('modelDiscoveryDescription')}</p></div><Button size="sm" variant="outline" loading={discovering} loadingText={t('discoveringModels')} disabled={!canDiscover} onClick={() => void discover()}><CloudDownload size={16} />{t('syncModels')}</Button></div>
    {unsupportedCount ? <p className="text-xs text-muted-foreground">{t('unsupportedModelsHidden', { count: unsupportedCount })}</p> : null}
    <div className="dashboard-record-list" aria-busy={loadingCatalog}>{loadingCatalog ? <div className="space-y-3 p-3"><Skeleton className="h-9 w-full" /><Skeleton className="h-9 w-full" /></div> : catalog.length === 0 ? <EmptyState title={t('noModels')} /> : catalog.map((model) => { const source = selected[model.provider_model_id]; return <label key={model.provider_model_id} className="dashboard-record flex min-h-12 cursor-pointer items-center gap-3"><Checkbox checked={Boolean(source)} onChange={(event) => setSelected((current) => { const next = { ...current }; event.target.checked ? next[model.provider_model_id] = 'manual' : delete next[model.provider_model_id]; return next; })} /><span className="min-w-0 flex-1"><span className="block text-sm font-medium">{model.display_name}</span><span className="mono block truncate text-xs text-muted-foreground">{model.provider_model_id}</span></span>{source ? <span className="status-badge">{t(`modelSource.${source}`)}</span> : null}</label>; })}</div>
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]"><label className="field"><span className="field-label">{t('manualModelPlaceholder')}</span><Input className="mono" value={manualId} onChange={(event) => setManualId(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addManual(); } }} /></label><Button className="self-end" variant="outline" onClick={addManual}>{t('addCatalogModel')}</Button></div>
  </div><DialogFooter className="border-t px-6 py-4"><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={!canSave} onClick={() => void save()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>;
}
