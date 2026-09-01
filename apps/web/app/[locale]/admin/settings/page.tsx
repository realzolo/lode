'use client';

import { useCallback, useEffect, useState } from 'react';
import { Save } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { TableEmptyState } from '@/components/ui/empty-state';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { apiErrorMessage, fetchPlatformSettings, updatePlatformSettings } from '@/lib/api';
import type { PlatformSettings } from '@/lib/types';

export default function SettingsPage() {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [language, setLanguage] = useState<'en' | 'zh'>('en');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async ({ background = false, preserveError = false } = {}) => {
    if (!background) setLoading(true);
    if (!preserveError) setError('');
    try {
      const value = await fetchPlatformSettings();
      setSettings(value);
      setLanguage(value.ai_output_language);
      if (!preserveError) setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      if (!background) setLoading(false);
    }
  }, [tc]);
  useEffect(() => { void load(); }, [load]);
  async function save() {
    if (!settings) return;
    setSaving(true);
    try {
      const value = await updatePlatformSettings({ ai_output_language: language, expected_revision: settings.revision });
      setSettings(value);
      toast.success(t('saved'));
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
      await load({ background: true, preserveError: true });
    } finally {
      setSaving(false);
    }
  }
  const labels = { en: t('english'), zh: t('chinese') };
  return <main className="dashboard-page settings-page max-w-2xl space-y-6">
    <header className="dashboard-page-header"><div><h1 className="page-title">{t('title')}</h1><p className="page-subtitle">{t('subtitle')}</p></div></header>
    {error && <p className="dashboard-feedback" role="alert">{error}</p>}
    {loading ? <SettingsSkeleton /> : !settings ? <TableEmptyState title={tc('requestFailed')} action={<Button size="sm" variant="outline" onClick={() => void load()}>{tc('retry')}</Button>} /> : <section className="dashboard-form-panel">
      <label className="field"><span className="field-label">{t('aiOutputLanguage')}</span><Select value={language} onChange={(event) => setLanguage(event.target.value as 'en' | 'zh')}>{settings.supported_languages.map((value) => <option key={value} value={value}>{labels[value]}</option>)}</Select></label>
      <p className="text-sm text-muted-foreground">{t('aiOutputLanguageHelp')}</p>
      <div className="flex justify-end"><Button variant="primary" loading={saving} loadingText={tc('saving')} disabled={saving || language === settings.ai_output_language} onClick={() => void save()}><Save size={16} />{tc('save')}</Button></div>
    </section>}
  </main>;
}

function SettingsSkeleton() {
  return <section className="dashboard-form-panel" aria-busy="true">
    <div className="space-y-3"><Skeleton className="h-4 w-32" /><Skeleton className="h-9 w-full" /></div>
    <Skeleton className="h-4 w-72" />
    <div className="flex justify-end"><Skeleton className="h-9 w-20" /></div>
  </section>;
}
