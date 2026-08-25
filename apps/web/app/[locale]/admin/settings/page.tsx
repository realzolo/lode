'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Activity } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  changePassword,
  createAiModel,
  createGitCredential,
  createGitRepo,
  deleteAiModel,
  deleteGitCredential,
  deleteGitRepo,
  fetchSettings,
  testAiModel,
  updateAiOutputLanguage,
  updateAiModel,
  updateGitCredential,
  updateGitRepo,
  type AiModelInput,
  type GitCredentialInput,
  type GitCredentialRow,
  type GitRepoInput,
  type GitRepoRow,
  type GlobalSettings,
} from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { IconCheck, IconPlus, IconEdit2, IconTrash2 } from '@/components/icons';

export default function SettingsPage() {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const tu = useTranslations('users');
  const ta = useTranslations('account');
  const [settings, setSettings] = useState<GlobalSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isAdmin = useUser().isAdmin;

  useEffect(() => {
    let active = true;
    fetchSettings()
      .then((d) => {
        if (!active) return;
        setSettings(d);
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
      {loading && (
        <div className="stack" style={{ marginTop: 24 }} aria-busy="true">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="stack">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-3.5 w-56" />
              <div className="stack" style={{ marginTop: 8, gap: 8 }}>
                <div className="row-between">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-5 w-16" />
                </div>
                <div className="row-between">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-4 w-24" />
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
      {error && !settings && (
        <div className="dashboard-error" role="alert">
          <p className="muted" style={{ color: 'var(--danger)' }}>{error}</p>
          <Button variant="outline" size="sm" onClick={() => void reload()}>{tc('retry')}</Button>
        </div>
      )}
      {error && settings && (
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
                <span className="mono">
                  {c.username || '—'} · {c.auth_type}
                  {c.readonly ? ' · readonly' : ' · rw'}
                </span>
                <Badge variant={c.readonly ? 'success' : 'warning'}>
                  {c.readonly ? 'readonly' : 'rw'}
                </Badge>
              </div>
            ))}
            {isAdmin && (
              <GitCredentialManager settings={settings} onChanged={reload} onError={setError} />
            )}
          </Card>

          <Card>
            <h2 className="section-title">{t('repoRegistry')}</h2>
            {settings.git_repos.length === 0 && <p className="muted">{tc('empty')}</p>}
            <div className="stack">
              {settings.git_repos.map((r) => (
                <div key={r.id} className="row-between">
                  <span className="mono">{r.name}</span>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {r.repo_type} · {r.repo_url}
                  </span>
                </div>
              ))}
            </div>
            {isAdmin && (
              <GitRepoManager settings={settings} onChanged={reload} onError={setError} />
            )}
          </Card>

          <Card>
            <h2 className="section-title">{t('aiOutputLanguage')}</h2>
            <p className="page-subtitle">{t('aiOutputLanguageDesc')}</p>
            {isAdmin ? (
              <AiOutputLanguageManager
                settings={settings}
                onChanged={reload}
                onError={setError}
              />
            ) : (
              <p className="mono" style={{ marginTop: 12 }}>
                {formatOutputLanguage(settings.ai_output_language, t)}
              </p>
            )}
          </Card>

          <Card>
            <h2 className="section-title">{t('aiModel')}</h2>
            <p className="page-subtitle">{t('aiModelDesc')}</p>
            {settings.ai_model_configs.length === 0 && <p className="muted">{tc('empty')}</p>}
            {settings.ai_model_configs.map((m) => (
              <div key={m.id} className="row-between" style={{ marginTop: 8 }}>
                <span className="mono">
                  {m.provider} · {m.model}
                </span>
                {m.is_default && <Badge variant="accent">default</Badge>}
              </div>
            ))}
            {isAdmin && (
              <AiModelManager
                settings={settings}
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

function formatOutputLanguage(
  language: GlobalSettings['ai_output_language'],
  t: ReturnType<typeof useTranslations>
): string {
  return language === 'zh' ? t('simplifiedChinese') : t('english');
}

function AiOutputLanguageManager({
  settings,
  onChanged,
  onError,
}: {
  settings: GlobalSettings;
  onChanged: () => Promise<void> | void;
  onError: (msg: string | null) => void;
}) {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const [language, setLanguage] = useState<GlobalSettings['ai_output_language']>(
    settings.ai_output_language
  );
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLanguage(settings.ai_output_language);
  }, [settings.ai_output_language]);

  async function save() {
    setBusy(true);
    onError(null);
    try {
      await updateAiOutputLanguage(language);
      await onChanged();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="row" style={{ marginTop: 12, gap: 8, alignItems: 'center' }}>
      <label htmlFor="ai-output-language" style={{ fontSize: 13 }}>
        {t('outputLanguage')}
      </label>
      <div style={{ width: 200 }}>
        <Select
          id="ai-output-language"
          value={language}
          onChange={(event) =>
            setLanguage(event.target.value as GlobalSettings['ai_output_language'])
          }
          disabled={busy}
        >
          {settings.supported_ai_output_languages.map((option) => (
            <option key={option} value={option}>
              {formatOutputLanguage(option, t)}
            </option>
          ))}
        </Select>
      </div>
      <Button
        size="sm"
        variant="primary"
        onClick={save}
        disabled={busy || language === settings.ai_output_language}
      >
        <IconCheck size={14} /> {tc('save')}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Admin-only AI model CRUD
// ---------------------------------------------------------------------------

function AiModelManager({
  settings,
  onChanged,
  onError,
}: {
  settings: GlobalSettings;
  onChanged: () => Promise<void> | void;
  onError: (msg: string | null) => void;
}) {
  const t = useTranslations('settings');
  const tu = useTranslations('users');
  const tc = useTranslations('common');
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [provider, setProvider] = useState('openai');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKeyRef, setApiKeyRef] = useState('');
  const [model, setModel] = useState('');
  const [isDefault, setIsDefault] = useState(true);
  const [busy, setBusy] = useState(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [testingId, setTestingId] = useState<number | null>(null);

  function resetForm() {
    setEditingId(null);
    setProvider('openai');
    setBaseUrl('');
    setApiKeyRef('');
    setModel('');
    setIsDefault(true);
  }

  function startEdit(m: GlobalSettings['ai_model_configs'][number]) {
    setEditingId(m.id);
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
    onError(null);
    try {
      await deleteAiModel(id);
      await onChanged();
    } catch (e) {
      onError(String(e));
      throw e;
    }
  }

  async function handleTest(id: number) {
    setTestingId(id);
    onError(null);
    try {
      const result = await testAiModel(id);
      if (!result.available) onError(result.error_detail || result.error_code || t('modelUnavailable'));
      await onChanged();
    } catch (e) {
      onError(String(e));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <div style={{ marginTop: 16 }}>
      <Button size="sm" variant="primary" onClick={() => { resetForm(); setShowForm(true); }}>
        <IconPlus size={14} /> {t('addModel')}
      </Button>

      <Dialog open={showForm} onOpenChange={setShowForm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingId != null ? tc('save') : t('createModel')}</DialogTitle>
            <DialogDescription>{t('aiModelDesc')}</DialogDescription>
          </DialogHeader>
          <div className="stack">
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
            <div className="row" style={{ gap: 8, fontSize: 13, alignItems: 'center' }}>
              <Switch
                id="ai-model-is-default"
                checked={isDefault}
                onCheckedChange={setIsDefault}
              />
              <label htmlFor="ai-model-is-default" style={{ cursor: 'pointer' }}>
                default
              </label>
            </div>
          </div>
          <DialogFooter>
            <Button size="sm" onClick={() => { resetForm(); setShowForm(false); }}>{tc('cancel')}</Button>
            <Button
              size="sm"
              variant="primary"
              onClick={handleSubmit}
              disabled={busy || !baseUrl || !model}
            >
              {editingId != null ? tc('save') : t('createModel')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="stack" style={{ marginTop: 12 }}>
        {settings.ai_model_configs.map((m) => (
          <div key={m.id} className="row-between" style={{ borderTop: '1px solid var(--color-4)', paddingTop: 10 }}>
            <div className="stack" style={{ gap: 4 }}>
              <span className="mono" style={{ fontSize: 13 }}>#{m.id} {m.provider} · {m.model}{m.is_default ? ' · default' : ''}</span>
              <span className="muted" style={{ fontSize: 12 }}>{m.base_url}</span>
              {m.last_test_error_detail && <span className="muted" style={{ color: 'var(--danger)', fontSize: 12 }}>{m.last_test_error_detail}</span>}
            </div>
            <div className="row" style={{ gap: 6 }}>
              <Badge variant={m.last_test_status === 'available' ? 'success' : m.last_test_status === 'unavailable' ? 'danger' : 'default'}>
                {t(m.last_test_status === 'available' ? 'modelAvailable' : m.last_test_status === 'unavailable' ? 'modelUnavailable' : 'modelUntested')}
              </Badge>
              <Button size="sm" onClick={() => void handleTest(m.id)} disabled={testingId !== null}>
                <Activity size={14} /> {testingId === m.id ? t('testingModel') : t('testModel')}
              </Button>
              <Button size="sm" onClick={() => startEdit(m)}><IconEdit2 size={14} /> {tc('save')}</Button>
              <Button size="sm" variant="destructive" onClick={() => setDeleteId(m.id)}><IconTrash2 size={14} /> {tu('delete')}</Button>
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={deleteId != null}
        onOpenChange={(open) => !open && setDeleteId(null)}
        title={t('deleteModelTitle')}
        description={t('deleteModelDesc')}
        confirmLabel={tc('delete')}
        cancelLabel={tc('cancel')}
        destructive
        onConfirm={() => {
          if (deleteId != null) return handleDelete(deleteId);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Admin-only Git credential CRUD
// ---------------------------------------------------------------------------

function GitCredentialManager({
  settings,
  onChanged,
  onError,
}: {
  settings: GlobalSettings;
  onChanged: () => Promise<void> | void;
  onError: (msg: string | null) => void;
}) {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [authType, setAuthType] = useState('ssh');
  const [username, setUsername] = useState('');
  const [secretRef, setSecretRef] = useState('');
  const [readonly, setReadonly] = useState(true);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);

  function resetForm() {
    setEditingId(null);
    setAuthType('ssh');
    setUsername('');
    setSecretRef('');
    setReadonly(true);
    setNote('');
  }

  function startEdit(c: GitCredentialRow) {
    setEditingId(c.id);
    setAuthType(c.auth_type);
    setUsername(c.username);
    setSecretRef('');
    setReadonly(c.readonly);
    setNote(c.note);
    setShowForm(true);
  }

  async function handleSubmit() {
    setBusy(true);
    onError(null);
    // On edit, only send ``secret_ref`` when the operator actually typed one —
    // the backend keeps the existing secret otherwise.
    const payload: Partial<GitCredentialInput> = {
      auth_type: authType,
      username,
      readonly,
      note,
    };
    if (secretRef) payload.secret_ref = secretRef;
    try {
      if (editingId != null) {
        await updateGitCredential(editingId, payload);
      } else {
        await createGitCredential({ ...(payload as GitCredentialInput), secret_ref: secretRef });
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
    onError(null);
    try {
      await deleteGitCredential(id);
      await onChanged();
    } catch (e) {
      onError(String(e));
      throw e;
    }
  }

  return (
    <div style={{ marginTop: 16 }}>
      <Button size="sm" variant="primary" onClick={() => { resetForm(); setShowForm(true); }}>
        <IconPlus size={14} /> {t('addGitAccount')}
      </Button>

      <Dialog open={showForm} onOpenChange={setShowForm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingId != null ? t('editGitAccount') : t('createGitAccount')}</DialogTitle>
            <DialogDescription>{t('gitAccountDesc')}</DialogDescription>
          </DialogHeader>
          <div className="stack">
            <Select value={authType} onChange={(e) => setAuthType(e.target.value)}>
              <option value="ssh">{t('ssh')}</option>
              <option value="https">{t('https')}</option>
            </Select>
            <Input
              placeholder={t('username')}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <Input
              type="password"
              placeholder={t('secretRef')}
              value={secretRef}
              onChange={(e) => setSecretRef(e.target.value)}
            />
            <div className="row" style={{ gap: 8, fontSize: 13, alignItems: 'center' }}>
              <Switch
                id="git-credential-readonly"
                checked={readonly}
                onCheckedChange={setReadonly}
              />
              <label htmlFor="git-credential-readonly" style={{ cursor: 'pointer' }}>
                {t('readonly')}
              </label>
            </div>
            <Input
              placeholder={t('note')}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button size="sm" onClick={() => { resetForm(); setShowForm(false); }}>{tc('cancel')}</Button>
            <Button
              size="sm"
              variant="primary"
              onClick={handleSubmit}
              disabled={busy || !username || (!editingId && !secretRef)}
            >
              {editingId != null ? tc('save') : t('createGitAccount')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="stack" style={{ marginTop: 12 }}>
        {settings.git_credentials.map((c) => (
          <div key={c.id} className="row-between" style={{ borderTop: '1px solid var(--color-4)', paddingTop: 8 }}>
            <span className="mono" style={{ fontSize: 12 }}>
              #{c.id} {c.username || '—'} · {c.auth_type}
              {c.readonly ? ' · readonly' : ' · rw'}
              {c.has_secret ? ' · secret' : ''}
            </span>
            <div className="row" style={{ gap: 6 }}>
              <Button size="sm" onClick={() => startEdit(c)}><IconEdit2 size={14} /> {tc('edit')}</Button>
              <Button size="sm" variant="destructive" onClick={() => setDeleteId(c.id)}><IconTrash2 size={14} /> {tc('delete')}</Button>
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={deleteId != null}
        onOpenChange={(open) => !open && setDeleteId(null)}
        title={t('deleteGitAccountTitle')}
        description={t('deleteGitAccountDesc')}
        confirmLabel={tc('delete')}
        cancelLabel={tc('cancel')}
        destructive
        onConfirm={() => {
          if (deleteId != null) return handleDelete(deleteId);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Admin-only repository registry CRUD
// ---------------------------------------------------------------------------

const REPO_TYPES = ['github', 'gitlab', 'gitee', 'bitbucket', 'other'] as const;

function GitRepoManager({
  settings,
  onChanged,
  onError,
}: {
  settings: GlobalSettings;
  onChanged: () => Promise<void> | void;
  onError: (msg: string | null) => void;
}) {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [repoUrl, setRepoUrl] = useState('');
  const [defaultBranch, setDefaultBranch] = useState('main');
  const [repoType, setRepoType] = useState<string>('github');
  const [credentialId, setCredentialId] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);

  function resetForm() {
    setEditingId(null);
    setName('');
    setRepoUrl('');
    setDefaultBranch('main');
    setRepoType('github');
    setCredentialId('');
  }

  function startEdit(r: GitRepoRow) {
    setEditingId(r.id);
    setName(r.name);
    setRepoUrl(r.repo_url);
    setDefaultBranch(r.default_branch);
    setRepoType(r.repo_type);
    setCredentialId(r.credential_id != null ? String(r.credential_id) : '');
    setShowForm(true);
  }

  async function handleSubmit() {
    setBusy(true);
    onError(null);
    const payload: Partial<GitRepoInput> = {
      name,
      repo_url: repoUrl,
      default_branch: defaultBranch,
      repo_type: repoType,
      credential_id: credentialId ? Number(credentialId) : null,
    };
    try {
      if (editingId != null) {
        await updateGitRepo(editingId, payload);
      } else {
        await createGitRepo(payload as GitRepoInput);
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
    onError(null);
    try {
      await deleteGitRepo(id);
      await onChanged();
    } catch (e) {
      onError(String(e));
      throw e;
    }
  }

  return (
    <div style={{ marginTop: 16 }}>
      <Button size="sm" variant="primary" onClick={() => { resetForm(); setShowForm(true); }}>
        <IconPlus size={14} /> {t('addRepo')}
      </Button>

      <Dialog open={showForm} onOpenChange={setShowForm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingId != null ? t('editRepo') : t('createRepo')}</DialogTitle>
            <DialogDescription>{t('repoRegistry')}</DialogDescription>
          </DialogHeader>
          <div className="stack">
            <Input
              placeholder={t('repoName')}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Input
              placeholder={t('repoUrl')}
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
            />
            <Select value={repoType} onChange={(e) => setRepoType(e.target.value)}>
              {REPO_TYPES.map((rt) => (
                <option key={rt} value={rt}>{rt}</option>
              ))}
            </Select>
            <Input
              placeholder={t('defaultBranch')}
              value={defaultBranch}
              onChange={(e) => setDefaultBranch(e.target.value)}
            />
            <Select value={credentialId} onChange={(e) => setCredentialId(e.target.value)}>
              <option value="">{t('noCredential')}</option>
              {settings.git_credentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.username || '—'} · {c.auth_type}
                </option>
              ))}
            </Select>
          </div>
          <DialogFooter>
            <Button size="sm" onClick={() => { resetForm(); setShowForm(false); }}>{tc('cancel')}</Button>
            <Button
              size="sm"
              variant="primary"
              onClick={handleSubmit}
              disabled={busy || !name || !repoUrl}
            >
              {editingId != null ? tc('save') : t('createRepo')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="stack" style={{ marginTop: 12 }}>
        {settings.git_repos.map((r) => (
          <div key={r.id} className="row-between" style={{ borderTop: '1px solid var(--color-4)', paddingTop: 8 }}>
            <span className="mono" style={{ fontSize: 12 }}>
              #{r.id} {r.name} · {r.repo_type} · {r.default_branch}
            </span>
            <div className="row" style={{ gap: 6 }}>
              <Button size="sm" onClick={() => startEdit(r)}><IconEdit2 size={14} /> {tc('edit')}</Button>
              <Button size="sm" variant="destructive" onClick={() => setDeleteId(r.id)}><IconTrash2 size={14} /> {tc('delete')}</Button>
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={deleteId != null}
        onOpenChange={(open) => !open && setDeleteId(null)}
        title={t('deleteRepoTitle')}
        description={t('deleteRepoDesc')}
        confirmLabel={tc('delete')}
        cancelLabel={tc('cancel')}
        destructive
        onConfirm={() => {
          if (deleteId != null) return handleDelete(deleteId);
        }}
      />
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
