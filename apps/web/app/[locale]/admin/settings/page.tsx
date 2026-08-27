'use client';

import { useCallback, useEffect, useState } from 'react';
import { Save } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { apiErrorMessage, fetchPlatformSettings, updatePlatformSettings } from '@/lib/api';
import type { PlatformSettings } from '@/lib/types';

export default function SettingsPage() {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [language, setLanguage] = useState<'en' | 'zh'>('en');
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      const value = await fetchPlatformSettings();
      setSettings(value);
      setLanguage(value.ai_output_language);
      setError('');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function save() {
    if (!settings) return;
    try {
      const value = await updatePlatformSettings({ ai_output_language: language, expected_revision: settings.revision });
      setSettings(value);
      toast.success(t('saved'));
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
      await load();
    }
  }
  const labels = { en: t('english'), zh: t('chinese') };
  return <main className="max-w-2xl space-y-6">
    <header><p className="eyebrow">{t('title')}</p><h1 className="page-title">{t('title')}</h1><p className="page-subtitle">{t('subtitle')}</p></header>
    {error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    {!settings ? <p className="text-sm text-muted-foreground">{tc('loading')}</p> : <section className="space-y-5 border p-5">
      <label className="field"><span className="field-label">{t('aiOutputLanguage')}</span><Select value={language} onChange={(event) => setLanguage(event.target.value as 'en' | 'zh')}>{settings.supported_languages.map((value) => <option key={value} value={value}>{labels[value]}</option>)}</Select></label>
      <p className="text-sm text-muted-foreground">{t('aiOutputLanguageHelp')}</p>
      <div className="flex justify-end"><Button variant="primary" disabled={language === settings.ai_output_language} onClick={() => void save()}><Save size={16} />{tc('save')}</Button></div>
    </section>}
  </main>;
}
