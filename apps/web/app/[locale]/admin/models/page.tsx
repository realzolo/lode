'use client';

import { useCallback, useEffect, useState } from 'react';
import { Activity, Plus, RefreshCw, ScanSearch } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { createModelDeployment, createProviderAccount, fetchModelDeployments, fetchProviderAccounts, introspectProviderModels, testModelDeployment, testProviderAccount } from '@/lib/api';
import type { ModelDeployment, ProviderAccount } from '@/lib/types';

export default function ModelsPage() {
  const t = useTranslations('admin');
  const tc = useTranslations('common');
  const [providers, setProviders] = useState<ProviderAccount[]>([]);
  const [deployments, setDeployments] = useState<ModelDeployment[]>([]);
  const [error, setError] = useState('');
  const [providerOpen, setProviderOpen] = useState(false);
  const [deploymentOpen, setDeploymentOpen] = useState(false);
  const [form, setForm] = useState({ name: '', provider_kind: 'openai', base_url: 'https://api.openai.com/v1', credential: '', data_processing_policy_revision: 'current', data_residency: 'provider', retention_mode: 'none' });
  const [deployment, setDeployment] = useState({ provider_account_id: '', provider_model_id: '', display_name: '', max_input_tokens: '128000', max_output_tokens: '8192', tokenizer_id: 'cl100k_base' });
  const load = useCallback(async () => {
    try { const [accounts, models] = await Promise.all([fetchProviderAccounts(), fetchModelDeployments()]); setProviders(accounts); setDeployments(models); setError(''); }
    catch (cause) { setError(String(cause)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function addProvider() {
    try { await createProviderAccount({ ...form, rate_limit_policy: {}, cost_policy: {} }); setProviderOpen(false); await load(); }
    catch (cause) { setError(String(cause)); toast.error(String(cause)); }
  }
  async function addDeployment() {
    try {
      await createModelDeployment(Number(deployment.provider_account_id), {
        provider_model_id: deployment.provider_model_id,
        display_name: deployment.display_name || deployment.provider_model_id,
        capabilities: {}, max_input_tokens: Number(deployment.max_input_tokens), max_output_tokens: Number(deployment.max_output_tokens), tokenizer_id: deployment.tokenizer_id,
        provider_revision: 'current', quality_baseline_revision: 'candidate', cost_policy_revision: 'current', rate_limit_policy_revision: 'current',
      });
      setDeploymentOpen(false); await load();
    } catch (cause) { setError(String(cause)); toast.error(String(cause)); }
  }

  async function runAction(action: () => Promise<unknown>, reload = false) {
    try {
      await action();
      if (reload) await load();
    } catch (cause) {
      setError(String(cause));
      toast.error(String(cause));
    }
  }

  return <main className="space-y-8"><header className="flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow">{t('providerPortfolio')}</p><h1 className="page-title">{t('models')}</h1><p className="page-subtitle">{t('modelsSubtitle')}</p></div><Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button></header>{error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    <section className="space-y-3"><div className="flex items-center justify-between"><h2 className="text-base font-semibold">{t('providerAccounts')}</h2><Button size="sm" onClick={() => setProviderOpen(true)}><Plus size={15} />{t('addProvider')}</Button></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('kind')}</th><th>{t('endpoint')}</th><th>{t('verification')}</th><th /></tr></thead><tbody>{providers.map((row) => <tr key={row.id}><td className="font-medium">{row.name}</td><td>{row.provider_kind}</td><td className="mono text-xs">{row.base_url}</td><td>{row.verification_status}</td><td><div className="flex justify-end gap-1"><Button size="icon" variant="ghost" title={t('testProvider')} aria-label={t('testProvider')} onClick={() => void runAction(() => testProviderAccount(row.id), true)}><Activity size={16} /></Button><Button size="icon" variant="ghost" title={t('introspectModels')} aria-label={t('introspectModels')} onClick={() => void runAction(() => introspectProviderModels(row.id))}><ScanSearch size={16} /></Button></div></td></tr>)}</tbody></table></div></div></section>
    <section className="space-y-3"><div className="flex items-center justify-between"><h2 className="text-base font-semibold">{t('deployments')}</h2><Button size="sm" onClick={() => setDeploymentOpen(true)} disabled={!providers.length}><Plus size={15} />{t('addDeployment')}</Button></div><div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('providerModel')}</th><th>{t('context')}</th><th>{t('tokenizer')}</th><th>{t('availability')}</th><th /></tr></thead><tbody>{deployments.map((row) => <tr key={row.id}><td className="font-medium">{row.display_name}</td><td className="mono text-xs">{row.provider_model_id}</td><td>{row.max_input_tokens.toLocaleString()}</td><td>{row.tokenizer_id}</td><td>{row.availability_state}</td><td><Button size="icon" variant="ghost" title={t('probeDeployment')} aria-label={t('probeDeployment')} onClick={() => void runAction(() => testModelDeployment(row.id), true)}><Activity size={16} /></Button></td></tr>)}</tbody></table></div></div></section>
    <Dialog open={providerOpen} onOpenChange={setProviderOpen}><DialogContent><DialogHeader><DialogTitle>{t('addProviderAccount')}</DialogTitle></DialogHeader><div className="grid gap-3 sm:grid-cols-2"><Input placeholder={t('name')} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /><Select value={form.provider_kind} onChange={(e) => setForm({ ...form, provider_kind: e.target.value })}><option value="openai">OpenAI</option><option value="openai_compatible">OpenAI compatible</option><option value="anthropic">Anthropic</option></Select><Input className="sm:col-span-2" placeholder={t('httpsBaseUrl')} value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} /><Input className="sm:col-span-2" type="password" placeholder={t('credential')} value={form.credential} onChange={(e) => setForm({ ...form, credential: e.target.value })} /></div><DialogFooter><Button variant="outline" onClick={() => setProviderOpen(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!form.name || !form.credential} onClick={() => void addProvider()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={deploymentOpen} onOpenChange={setDeploymentOpen}><DialogContent><DialogHeader><DialogTitle>{t('addDeploymentTitle')}</DialogTitle></DialogHeader><div className="grid gap-3 sm:grid-cols-2"><Select value={deployment.provider_account_id} onChange={(e) => setDeployment({ ...deployment, provider_account_id: e.target.value })}><option value="">{t('provider')}</option>{providers.filter((row) => row.state === 'active').map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</Select><Input placeholder={t('providerModelId')} value={deployment.provider_model_id} onChange={(e) => setDeployment({ ...deployment, provider_model_id: e.target.value })} /><Input placeholder={t('displayName')} value={deployment.display_name} onChange={(e) => setDeployment({ ...deployment, display_name: e.target.value })} /><Input placeholder={t('tokenizerId')} value={deployment.tokenizer_id} onChange={(e) => setDeployment({ ...deployment, tokenizer_id: e.target.value })} /><Input type="number" placeholder={t('maxInputTokens')} value={deployment.max_input_tokens} onChange={(e) => setDeployment({ ...deployment, max_input_tokens: e.target.value })} /><Input type="number" placeholder={t('maxOutputTokens')} value={deployment.max_output_tokens} onChange={(e) => setDeployment({ ...deployment, max_output_tokens: e.target.value })} /></div><DialogFooter><Button variant="outline" onClick={() => setDeploymentOpen(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!deployment.provider_account_id || !deployment.provider_model_id} onClick={() => void addDeployment()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>
  </main>;
}
