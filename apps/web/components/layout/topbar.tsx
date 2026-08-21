'use client';

import { useEffect, useState } from 'react';
import { useTranslations, useLocale } from 'next-intl';
import { useTheme } from 'next-themes';
import { useRouter, usePathname } from '@/lib/navigation';
import { CommandPalette } from '@/components/cmdk';
import { Select } from '@/components/ui/select';
import { fetchApplications, clearToken } from '@/lib/api';
import type { Application } from '@/lib/types';

export function Topbar() {
  const t = useTranslations();
  const theme = useTheme();
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const [apps, setApps] = useState<Application[]>([]);

  useEffect(() => {
    fetchApplications().then(setApps).catch(() => setApps([]));
  }, []);

  const toggleTheme = () =>
    theme.setTheme(theme.resolvedTheme === 'dark' ? 'light' : 'dark');

  const toggleLocale = () =>
    router.replace(pathname, { locale: locale === 'zh' ? 'en' : 'zh' });

  const handleLogout = () => {
    clearToken();
    router.replace('/login');
  };

  return (
    <header className="topbar">
      <Select
        aria-label={t('common.appName')}
        className="app-select"
        value=""
        onChange={(e) => {
          if (e.target.value) router.push(`/applications/${e.target.value}`);
        }}
      >
        <option value="" disabled>
          {t('common.appName')}…
        </option>
        {apps.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
      </Select>

      <div className="topbar-right">
        <CommandPalette />
        <button
          className="icon-btn"
          aria-label={t('common.language')}
          onClick={toggleLocale}
          title={t('common.language')}
        >
          {locale === 'zh' ? '中' : 'EN'}
        </button>
        <button
          className="icon-btn"
          aria-label={t('common.theme')}
          onClick={toggleTheme}
          title={t('common.theme')}
        >
          {theme.resolvedTheme === 'dark' ? '☾' : '☀'}
        </button>
        <button
          className="icon-btn"
          aria-label="logout"
          onClick={handleLogout}
          title="logout"
        >
          ⏻
        </button>
      </div>
    </header>
  );
}
