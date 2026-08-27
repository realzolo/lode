'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, Pencil, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import {
  createProviderAccount,
  discoverProviderModels,
  discoverSavedProviderModels,
  fetchProviderAccounts,
  testProviderAccountModel,
  updateProviderAccount,
} from '@/lib/api';
import type { ProviderAccount } from '@/lib/types';

const CATALOG_MODELS = [
  { provider_model_id: 'gpt-5.6-sol', display_name: 'GPT-5.6 Sol' },
  { provider_model_id: 'gpt-5.6-terra', display_name: 'GPT-5.6 Terra' },
  { provider_model_id: 'gpt-5.6-luna', display_name: 'GPT-5.6 Luna' },
];

type AccountForm = {
  name: string;
  baseUrl: string;
  credential: string;
  organizationRef: string;
  projectRef: string;
};

const emptyForm: AccountForm = {
  name: '', baseUrl: 'https://api.openai.com/v1', credential: '', organizationRef: '', projectRef: '',
};

export default function ModelsPage() {
  const t = useTranslations('admin');
  const tc = useTranslations('common');
  const [accounts, setAccounts] = useState<ProviderAccount[]>([]);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<ProviderAccount | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setAccounts(await fetchProviderAccounts());
      setError('');
    } catch (cause) {
      setError(String(cause));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  function createAccount() {
    setEditing(null);
    setOpen(true);
  }

  function editAccount(account: ProviderAccount) {
    setEditing(account);
    setOpen(true);
  }

  return (
    <main className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">{t('providerAccounts')}</h1>
          <p className="page-subtitle">{t('modelsSubtitle')}</p>
        </div>
        <div className="flex gap-2">
          <Button size="icon" variant="outline" title={tc('refresh')} aria-label={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
          <Button size="sm" onClick={createAccount}><Plus size={16} />{t('addAccount')}</Button>
        </div>
      </header>
      {error ? <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p> : null}
      <section className="space-y-3">
        {accounts.length === 0 ? <p className="py-10 text-center text-sm text-muted-foreground">{t('noAccounts')}</p> : accounts.map((account) => (
          <article key={account.id} className="border-b pb-5 last:border-0">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="font-semibold">{account.name}</h2>
                <p className="mono text-xs text-muted-foreground">{account.base_url}</p>
              </div>
              <Button size="icon" variant="ghost" title={t('editAccount')} aria-label={t('editAccount')} onClick={() => editAccount(account)}><Pencil size={16} /></Button>
            </div>
            <div className="mt-3 overflow-x-auto border">
              <table className="table min-w-[640px]"><thead><tr><th>{t('providerModel')}</th><th>{t('source')}</th><th>{t('availability')}</th><th>{t('state')}</th><th /></tr></thead><tbody>
                {account.models.map((model) => <tr key={model.id}>
                  <td><span className="font-medium">{model.display_name}</span><span className="ml-2 mono text-xs text-muted-foreground">{model.provider_model_id}</span></td>
                  <td>{model.discovery_state}</td><td>{model.availability_state}</td><td>{model.state}</td>
                  <td><Button size="icon" variant="ghost" title={t('probeModel')} aria-label={t('probeModel')} disabled={model.state !== 'active'} onClick={() => void probe(account.id, model.id, load)}><Activity size={16} /></Button></td>
                </tr>)}
              </tbody></table>
            </div>
          </article>
        ))}
      </section>
      <AccountDialog open={open} account={editing} onOpenChange={setOpen} onSaved={load} />
    </main>
  );
}

async function probe(accountId: number, modelId: number, reload: () => Promise<void>) {
  try {
    await testProviderAccountModel(accountId, modelId);
    await reload();
  } catch (cause) {
    toast.error(String(cause));
  }
}

function AccountDialog({ open, account, onOpenChange, onSaved }: {
  open: boolean;
  account: ProviderAccount | null;
  onOpenChange: (value: boolean) => void;
  onSaved: () => Promise<void>;
}) {
  const t = useTranslations('admin');
  const tc = useTranslations('common');
  const [form, setForm] = useState<AccountForm>(emptyForm);
  const [discovered, setDiscovered] = useState<Array<{ provider_model_id: string; display_name: string }>>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [manual, setManual] = useState<string[]>([]);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    if (!open) return;
    setForm(account ? {
      name: account.name,
      baseUrl: account.base_url,
      credential: '',
      organizationRef: account.organization_ref || '',
      projectRef: account.project_ref || '',
    } : emptyForm);
    setDiscovered([]);
    setSelected(account?.models.filter((model) => model.state === 'active').map((model) => model.provider_model_id) || []);
    setManual(account?.models.filter((model) => model.state === 'active' && model.discovery_state === 'manual').map((model) => model.provider_model_id) || []);
  }, [account, open]);

  const candidates = useMemo(() => new Map([
    ...CATALOG_MODELS,
    ...discovered,
  ].map((model) => [model.provider_model_id, model])), [discovered]);

  function toggle(modelId: string, checked: boolean) {
    setSelected((current) => checked ? [...new Set([...current, modelId])] : current.filter((id) => id !== modelId));
    if (!checked) setManual((current) => current.filter((id) => id !== modelId));
  }

  async function sync() {
    if (!form.baseUrl || (!account && !form.credential)) return;
    setSyncing(true);
    try {
      const values = account && !form.credential
        ? await discoverSavedProviderModels(account.id)
        : await discoverProviderModels({
          base_url: form.baseUrl,
          credential: form.credential,
          organization_ref: form.organizationRef || undefined,
          project_ref: form.projectRef || undefined,
        });
      setDiscovered(values);
    } catch (cause) {
      toast.error(String(cause));
    } finally {
      setSyncing(false);
    }
  }

  function addManual(modelId: string) {
    if (!modelId) return;
    setSelected((current) => [...new Set([...current, modelId])]);
    setManual((current) => [...new Set([...current, modelId])]);
  }

  async function save() {
    if (!form.name || !form.baseUrl || selected.length === 0 || (!account && !form.credential)) return;
    const input = {
      name: form.name,
      base_url: form.baseUrl,
      ...(form.credential ? { credential: form.credential } : {}),
      organization_ref: form.organizationRef || null,
      project_ref: form.projectRef || null,
      model_ids: selected,
      manual_model_ids: manual,
    };
    try {
      if (account) await updateProviderAccount(account.id, input);
      else await createProviderAccount(input);
      onOpenChange(false);
      await onSaved();
    } catch (cause) {
      toast.error(String(cause));
    }
  }

  const upstreamIds = new Set(discovered.map((model) => model.provider_model_id));
  const visibleModels = [...candidates.values()].filter((model) => upstreamIds.has(model.provider_model_id) || manual.includes(model.provider_model_id));
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-w-3xl"><DialogHeader><DialogTitle>{account ? t('editAccount') : t('addAccount')}</DialogTitle></DialogHeader>
    <div className="grid gap-3 sm:grid-cols-2">
      <Input placeholder={t('accountName')} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
      <Input type="password" placeholder={account ? t('credentialOptional') : t('credential')} value={form.credential} onChange={(event) => setForm({ ...form, credential: event.target.value })} />
      <Input className="sm:col-span-2 mono" placeholder={t('httpsBaseUrl')} value={form.baseUrl} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} />
      <Input placeholder={t('organizationOptional')} value={form.organizationRef} onChange={(event) => setForm({ ...form, organizationRef: event.target.value })} />
      <Input placeholder={t('projectOptional')} value={form.projectRef} onChange={(event) => setForm({ ...form, projectRef: event.target.value })} />
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4"><h3 className="text-sm font-semibold">{t('accountModels')}</h3><Button size="sm" variant="outline" disabled={syncing || !form.baseUrl || (!account && !form.credential)} onClick={() => void sync()}><RefreshCw size={15} />{t('syncModels')}</Button></div>
    <div className="max-h-72 overflow-y-auto border">
      {visibleModels.length ? visibleModels.map((model) => <label key={model.provider_model_id} className="flex items-center gap-3 border-b px-3 py-2 text-sm last:border-0"><input type="checkbox" checked={selected.includes(model.provider_model_id)} onChange={(event) => toggle(model.provider_model_id, event.target.checked)} /><span className="font-medium">{model.display_name}</span><span className="mono text-xs text-muted-foreground">{model.provider_model_id}</span>{manual.includes(model.provider_model_id) ? <span className="ml-auto text-xs text-muted-foreground">{t('manual')}</span> : null}</label>) : <p className="p-4 text-sm text-muted-foreground">{t('syncToChoose')}</p>}
    </div>
    <Select value="" onChange={(event) => addManual(event.target.value)}><option value="">{t('addCatalogModel')}</option>{CATALOG_MODELS.filter((model) => !selected.includes(model.provider_model_id)).map((model) => <option key={model.provider_model_id} value={model.provider_model_id}>{model.display_name}</option>)}</Select>
    <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!form.name || !form.baseUrl || !selected.length || (!account && !form.credential)} onClick={() => void save()}>{tc('save')}</Button></DialogFooter>
  </DialogContent></Dialog>;
}
