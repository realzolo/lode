'use client';

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, GitFork, Plus, RefreshCw, RotateCw } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import {
  createGitAccountAccessToken,
  createGitHubAppConnection,
  createGitProviderInstance,
  fetchGitAccountConnections,
  fetchGitProviderInstances,
  startGitOAuth,
  syncGitAccountConnection,
} from '@/lib/api';
import type { GitAccountConnection, GitProviderInstance } from '@/lib/types';

export default function GitAdministrationPage() {
  const t = useTranslations('git');
  const tc = useTranslations('common');
  const [providers, setProviders] = useState<GitProviderInstance[]>([]);
  const [accounts, setAccounts] = useState<GitAccountConnection[]>([]);
  const [dialog, setDialog] = useState<'provider' | 'account' | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [providerRows, accountRows] = await Promise.all([fetchGitProviderInstances(), fetchGitAccountConnections()]);
      setProviders(providerRows);
      setAccounts(accountRows);
      setError('');
    } catch (cause) { setError(String(cause)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function sync(account: GitAccountConnection) {
    try { await syncGitAccountConnection(account.id); await load(); toast.success(t('syncComplete')); } catch (cause) { toast.error(String(cause)); }
  }

  return <main className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="mb-2 text-sm text-muted-foreground">{t('eyebrow')}</p><h1 className="page-title">{t('title')}</h1><p className="page-subtitle">{t('subtitle')}</p></div>
      <Button size="icon" variant="outline" aria-label={tc('refresh')} title={tc('refresh')} onClick={() => void load()}><RefreshCw size={16} /></Button>
    </header>
    {error ? <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p> : null}
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold">{t('providers')}</h2><p className="mt-1 text-sm text-muted-foreground">{t('providersHelp')}</p></div><Button size="sm" onClick={() => setDialog('provider')}><Plus size={15} />{t('addProvider')}</Button></div>
      <div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('provider')}</th><th>{t('nativeAuth')}</th><th>{t('endpoint')}</th><th>{t('state')}</th></tr></thead><tbody>{providers.map((provider) => <tr key={provider.id}><td className="font-medium">{provider.name}</td><td className="capitalize">{provider.kind}</td><td>{provider.native_auth_kind ? t(provider.native_auth_kind) : t('tokenOnly')}</td><td className="mono text-xs">{provider.base_url}</td><td><Status value={provider.state} /></td></tr>)}{!providers.length ? <EmptyRow columns={5} value={t('noProviders')} /> : null}</tbody></table></div></div>
    </section>
    <section className="space-y-4 border-t pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold">{t('accounts')}</h2><p className="mt-1 text-sm text-muted-foreground">{t('accountsHelp')}</p></div><Button size="sm" disabled={!providers.some((provider) => provider.state === 'active')} onClick={() => setDialog('account')}><Plus size={15} />{t('addAccount')}</Button></div>
      <div className="operational-table"><div className="table-wrap"><table className="table"><thead><tr><th>{t('name')}</th><th>{t('provider')}</th><th>{t('account')}</th><th>{t('authentication')}</th><th>{t('repositories')}</th><th>{t('lastSynced')}</th><th /></tr></thead><tbody>{accounts.map((account) => <tr key={account.id}><td className="font-medium">{account.name}</td><td>{account.provider_name}</td><td><a className="hover:text-link" href={account.account_url} target="_blank" rel="noreferrer">{account.external_account_login}</a></td><td>{t(account.auth_mode)}</td><td>{account.repository_count}</td><td>{account.last_synced_at ? new Date(account.last_synced_at).toLocaleString() : t('notSynced')}</td><td><Button size="icon" variant="ghost" aria-label={t('sync')} title={t('sync')} disabled={account.state !== 'active'} onClick={() => void sync(account)}><RotateCw size={15} /></Button></td></tr>)}{!accounts.length ? <EmptyRow columns={7} value={t('noAccounts')} /> : null}</tbody></table></div></div>
    </section>
    <ProviderDialog open={dialog === 'provider'} onOpenChange={(value) => !value && setDialog(null)} onCreated={load} />
    <AccountDialog open={dialog === 'account'} onOpenChange={(value) => !value && setDialog(null)} providers={providers} onCreated={load} />
  </main>;
}

function ProviderDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; onCreated: () => Promise<void> }) {
  const t = useTranslations('git'); const tc = useTranslations('common');
  const [kind, setKind] = useState<'github' | 'gitlab' | 'gitee'>('github');
  const [name, setName] = useState(''); const [baseUrl, setBaseUrl] = useState(''); const [apiUrl, setApiUrl] = useState('');
  const [auth, setAuth] = useState<'none' | 'github_app' | 'oauth'>('none');
  const [appId, setAppId] = useState(''); const [privateKey, setPrivateKey] = useState(''); const [clientId, setClientId] = useState(''); const [clientSecret, setClientSecret] = useState(''); const [redirectUri, setRedirectUri] = useState('');
  useEffect(() => { setAuth(kind === 'github' ? 'github_app' : 'oauth'); }, [kind]);
  async function create() {
    try {
      await createGitProviderInstance({ kind, name, ...(baseUrl ? { base_url: baseUrl } : {}), ...(apiUrl ? { api_url: apiUrl } : {}), ...(kind === 'github' && auth === 'github_app' ? { github_app_id: appId, github_app_private_key: privateKey } : {}), ...(kind !== 'github' && auth === 'oauth' ? { oauth_client_id: clientId, oauth_client_secret: clientSecret, oauth_redirect_uri: redirectUri } : {}) });
      onOpenChange(false); await onCreated();
    } catch (cause) { toast.error(String(cause)); }
  }
  const needsGithubApp = kind === 'github' && auth === 'github_app'; const needsOAuth = kind !== 'github' && auth === 'oauth';
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[90vh] overflow-y-auto"><DialogHeader><DialogTitle>{t('addProvider')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('provider')}</span><Select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="github">GitHub</option><option value="gitlab">GitLab</option><option value="gitee">Gitee</option></Select></label><label className="field"><span className="field-label">{t('name')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label><div className="grid gap-3 sm:grid-cols-2"><label className="field"><span className="field-label">{t('baseUrl')}</span><Input placeholder={t('defaultValue')} value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label><label className="field"><span className="field-label">{t('apiUrl')}</span><Input placeholder={t('defaultValue')} value={apiUrl} onChange={(event) => setApiUrl(event.target.value)} /></label></div><label className="field"><span className="field-label">{t('nativeAuth')}</span><Select value={auth} onChange={(event) => setAuth(event.target.value as typeof auth)}><option value="none">{t('tokenOnly')}</option>{kind === 'github' ? <option value="github_app">{t('github_app')}</option> : <option value="oauth">{t('oauth')}</option>}</Select></label>{needsGithubApp ? <><label className="field"><span className="field-label">{t('githubAppId')}</span><Input value={appId} onChange={(event) => setAppId(event.target.value)} /></label><label className="field"><span className="field-label">{t('githubPrivateKey')}</span><textarea className="min-h-28 w-full rounded-md border bg-transparent px-3 py-2 font-mono text-sm" value={privateKey} onChange={(event) => setPrivateKey(event.target.value)} /></label></> : null}{needsOAuth ? <><label className="field"><span className="field-label">{t('oauthClientId')}</span><Input value={clientId} onChange={(event) => setClientId(event.target.value)} /></label><label className="field"><span className="field-label">{t('oauthClientSecret')}</span><Input type="password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} /></label><label className="field"><span className="field-label">{t('oauthRedirectUri')}</span><Input value={redirectUri} onChange={(event) => setRedirectUri(event.target.value)} /></label></> : null}</div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!name || (needsGithubApp && (!appId || !privateKey)) || (needsOAuth && (!clientId || !clientSecret || !redirectUri))} onClick={() => void create()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>;
}

function AccountDialog({ open, onOpenChange, providers, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; providers: GitProviderInstance[]; onCreated: () => Promise<void> }) {
  const t = useTranslations('git'); const tc = useTranslations('common');
  const [providerId, setProviderId] = useState(''); const [name, setName] = useState(''); const [method, setMethod] = useState<'access_token' | 'github_app' | 'oauth'>('access_token'); const [token, setToken] = useState(''); const [installationId, setInstallationId] = useState('');
  const provider = providers.find((value) => value.id === Number(providerId));
  useEffect(() => { if (provider?.native_auth_kind) setMethod(provider.native_auth_kind); else setMethod('access_token'); }, [provider]);
  async function create() {
    try {
      if (method === 'access_token') await createGitAccountAccessToken({ provider_instance_id: Number(providerId), name, access_token: token });
      else if (method === 'github_app') await createGitHubAppConnection({ provider_instance_id: Number(providerId), name, installation_id: installationId });
      else {
        const result = await startGitOAuth(Number(providerId), name);
        window.open(result.authorization_url, 'lode-git-oauth', 'popup,width=600,height=760');
        toast.success(t('oauthOpened'));
      }
      if (method !== 'oauth') { onOpenChange(false); await onCreated(); }
    } catch (cause) { toast.error(String(cause)); }
  }
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>{t('addAccount')}</DialogTitle></DialogHeader><div className="space-y-3"><label className="field"><span className="field-label">{t('provider')}</span><Select value={providerId} onChange={(event) => setProviderId(event.target.value)}><option value="">{t('selectProvider')}</option>{providers.filter((value) => value.state === 'active').map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}</Select></label><label className="field"><span className="field-label">{t('name')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label>{provider?.native_auth_available ? <label className="field"><span className="field-label">{t('authentication')}</span><Select value={method} onChange={(event) => setMethod(event.target.value as typeof method)}>{provider.kind === 'github' && provider.native_auth_kind === 'github_app' ? <option value="github_app">{t('github_app')}</option> : null}{provider.kind !== 'github' && provider.native_auth_kind === 'oauth' ? <option value="oauth">{t('oauth')}</option> : null}<option value="access_token">{t('access_token')}</option></Select></label> : null}{method === 'access_token' ? <label className="field"><span className="field-label">{t('readOnlyToken')}</span><Input type="password" value={token} onChange={(event) => setToken(event.target.value)} /></label> : null}{method === 'github_app' ? <label className="field"><span className="field-label">{t('githubInstallationId')}</span><Input value={installationId} onChange={(event) => setInstallationId(event.target.value)} /></label> : null}{method === 'oauth' ? <p className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">{t('oauthHelp')}</p> : null}</div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" disabled={!providerId || !name || (method === 'access_token' && !token) || (method === 'github_app' && !installationId)} onClick={() => void create()}>{method === 'oauth' ? t('continueOAuth') : tc('save')}</Button></DialogFooter></DialogContent></Dialog>;
}

function Status({ value }: { value: string }) { return <span className="inline-flex items-center gap-1 text-sm"><CheckCircle2 size={14} className={value === 'active' ? 'text-success' : 'text-muted-foreground'} />{value}</span>; }
function EmptyRow({ columns, value }: { columns: number; value: string }) { return <tr><td colSpan={columns} className="py-8 text-center text-sm text-muted-foreground"><GitFork className="mr-2 inline" size={15} />{value}</td></tr>; }
