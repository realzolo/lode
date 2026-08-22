'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import {
  changePassword,
  createAiModel,
  deleteAiModel,
  fetchApplications,
  fetchSettings,
  updateAiModel,
  type AiModelInput,
  type GlobalSettings,
} from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { IconCheck, IconPlus, IconEdit2, IconTrash2 } from '@/components/icons';
import type { Application } from '@/lib/types';

export default function SettingsPage() {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const tu = useTranslations('users');
  const ta = useTranslations('account');
  const [settings, setSettings] = useState<GlobalSettings | null>(null);
  const [apps, setApps] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isAdmin = useUser().isAdmin;

  useEffect(() => {
    let active = true;
    Promise.all([fetchSettings(), fetchApplications().catch(() => [] as Application[])])
      .then(([d, a]) => {
        if (!active) return;
        setSettings(d);
        setApps(a);
      })
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function reload() {
    setError(null);
    try {
      setSettings(await fetchSettings());
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <>
      <h1 className="page-title">{t('title')}</h1>
      {loading && <p className="muted">{tc('loading')}</p>}
      {error && (
        <p className="muted" style={{ color: 'var(--danger)' }}>
          {error}
        </p>
      )}

      {settings && (
        <div className="stack" style={{ marginTop: 24 }}>
          <Card>
            <h2 className="section-title">{t('gitAccount')}</h2>
            <p className="page-subtitle">{t('gitAccountDesc')}</p>
            {settings.git_credentials.length === 0 && <p className="muted">{tc('empty')}</p>}
            {settings.git_credentials.map((c) => (
              <div key={c.id} className="row-between" style={{ marginTop: 8 }}>
                <span className="mono">{c.username || '—'}</span>
                <Badge variant={c.readonly ? 'success' : 'warning'}>
                  {c.readonly ? 'readonly' : 'rw'}
                </Badge>
              </div>
            ))}
          </Card>

          <Card>
            <h2 className="section-title">{t('repoRegistry')}</h2>
            {settings.git_repos.length === 0 && <p className="muted">{tc('empty')}</p>}
            <div className="stack">
              {settings.git_repos.map((r) => (
                <div key={r.id} className="row-between">
                  <span className="mono">{r.name}</span>
                  <span className="muted" style={{ fontSize: 12 }}>{r.repo_url}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <h2 className="section-title">{t('aiModel')}</h2>
            <p className="page-subtitle">{t('aiModelDesc')}</p>
            {settings.ai_model_configs.length === 0 && <p className="muted">{tc('empty')}</p>}
            {settings.ai_model_configs.map((m) => (
              <div key={m.id} className="row-between" style={{ marginTop: 8 }}>
                <span className="mono">
                  {m.provider} · {m.model}
                  {m.scope === 'application' ? ` (app ${m.application_id})` : ' (global)'}
                </span>
                {m.is_default && <Badge variant="accent">default</Badge>}
              </div>
            ))}
            {isAdmin && (
              <AiModelManager
                settings={settings}
                apps={apps}
                onChanged={reload}
                onError={setError}
              />
            )}
          </Card>

          <AccountSection />
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Admin-only AI model CRUD
// ---------------------------------------------------------------------------

function AiModelManager({
  settings,
  apps,
  onChanged,
  onError,
}: {
  settings: GlobalSettings;
  apps: Application[];
  onChanged: () => Promise<void> | void;
  onError: (msg: string | null) => void;
}) {
  const t = useTranslations('settings');
  const tu = useTranslations('users');
  const tc = useTranslations('common');
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [scope, setScope] = useState('global');
  const [applicationId, setApplicationId] = useState<string>('');
  const [provider, setProvider] = useState('openai');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKeyRef, setApiKeyRef] = useState('');
  const [model, setModel] = useState('');
  const [isDefault, setIsDefault] = useState(true);
  const [busy, setBusy] = useState(false);

  function resetForm() {
    setEditingId(null);
    setScope('global');
    setApplicationId('');
    setProvider('openai');
    setBaseUrl('');
    setApiKeyRef('');
    setModel('');
    setIsDefault(true);
  }

  function startEdit(m: GlobalSettings['ai_model_configs'][number]) {
    setEditingId(m.id);
    setScope(m.scope);
    setApplicationId(m.application_id != null ? String(m.application_id) : '');
    setProvider(m.provider);
    setBaseUrl(m.base_url);
    setApiKeyRef('');
    setModel(m.model);
    setIsDefault(m.is_default);
    setShowForm(true);
  }

  async function handleSubmit() {
    setBusy(true);
    onError(null);
    const payload: AiModelInput = {
      scope,
      application_id: scope === 'application' && applicationId ? Number(applicationId) : null,
      provider,
      base_url: baseUrl,
      api_key_ref: apiKeyRef,
      model,
      is_default: isDefault,
    };
    try {
      if (editingId != null) {
        await updateAiModel(editingId, payload);
      } else {
        await createAiModel(payload);
      }
      resetForm();
      setShowForm(false);
      await onChanged();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm(t('aiModel') + '?')) return;
    onError(null);
    try {
      await deleteAiModel(id);
      await onChanged();
    } catch (e) {
      onError(String(e));
    }
  }

  return (
    <div style={{ marginTop: 16 }}>
      <Button size="sm" variant="primary" onClick={() => { resetForm(); setShowForm((v) => !v); }}>
        <IconPlus size={14} /> {t('addModel')}
      </Button>

      {showForm && (
        <div className="stack" style={{ marginTop: 12 }}>
          <Select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="global">{t('aiModel')} · global</option>
            <option value="application">application</option>
          </Select>
          {scope === 'application' && (
            <Select value={applicationId} onChange={(e) => setApplicationId(e.target.value)}>
              <option value="">— select app —</option>
              {apps.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
          )}
          <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="openai">openai</option>
            <option value="anthropic">anthropic</option>
          </Select>
          <Input
            placeholder="base_url (e.g. https://api.openai.com/v1)"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <Input
            placeholder="api_key_ref (env://OPENAI_API_KEY or literal)"
            value={apiKeyRef}
            onChange={(e) => setApiKeyRef(e.target.value)}
          />
          <Input
            placeholder="model (e.g. gpt-4o-mini)"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
          <label className="row" style={{ gap: 8, fontSize: 13 }}>
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            default for this scope
          </label>
          <div className="row" style={{ gap: 8 }}>
            <Button size="sm" variant="primary" onClick={handleSubmit} disabled={busy || !baseUrl || !model || (scope === 'application' && !applicationId)}>
              {editingId != null ? tc('save') : t('createModel')}
            </Button>
            {editingId != null && (
              <Button size="sm" onClick={() => { resetForm(); setShowForm(false); }}>{tc('cancel')}</Button>
            )}
          </div>
        </div>
      )}

      <div className="stack" style={{ marginTop: 12 }}>
        {settings.ai_model_configs.map((m) => (
          <div key={m.id} className="row-between" style={{ borderTop: '1px solid var(--color-4)', paddingTop: 8 }}>
            <span className="mono" style={{ fontSize: 12 }}>
              #{m.id} {m.provider} · {m.model} · {m.scope}
              {m.scope === 'application' ? ` (${m.application_id})` : ''}
              {m.is_default ? ' · default' : ''}
            </span>
            <div className="row" style={{ gap: 6 }}>
              <Button size="sm" onClick={() => startEdit(m)}><IconEdit2 size={14} /> {tc('save')}</Button>
              <Button size="sm" variant="primary" onClick={() => handleDelete(m.id)}><IconTrash2 size={14} /> {tu('delete')}</Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Self-service password change (all authenticated users)
// ---------------------------------------------------------------------------

function AccountSection() {
  const ta = useTranslations('account');
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await changePassword(current, next);
      setMsg('ok');
      setCurrent('');
      setNext('');
    } catch (err) {
      setMsg(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h2 className="section-title">{ta('title')}</h2>
      <p className="page-subtitle">{ta('subtitle')}</p>
      <form className="stack" style={{ maxWidth: 360 }} onSubmit={handleSubmit}>
        <Input
          type="password"
          placeholder={ta('currentPassword')}
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <Input
          type="password"
          placeholder={ta('newPassword')}
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        {msg && (
          <p className="muted" style={{ color: msg === 'ok' ? 'var(--success)' : 'var(--danger)', fontSize: 13 }}>
            {msg === 'ok' ? (
              <span className="row" style={{ gap: 6 }}>
                <IconCheck size={14} /> {ta('update')}
              </span>
            ) : (
              msg
            )}
          </p>
        )}
        <Button variant="primary" type="submit" disabled={busy || !current || next.length < 8}>
          {ta('update')}
        </Button>
      </form>
    </Card>
  );
}
