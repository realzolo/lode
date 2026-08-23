'use client';

import { useEffect, useState } from 'react';
import { useTranslations, useLocale } from 'next-intl';
import { useTheme } from 'next-themes';
import { toast } from 'sonner';
import { useRouter, usePathname } from '@/lib/navigation';
import { CommandPalette } from '@/components/cmdk';
import { useUser } from '@/lib/user-context';
import { IconGlobe, IconSun, IconMoon, IconLogOut } from '@/components/icons';

// The app switcher used to live here as a <Select>. It has been removed:
// applications are now reached from the Dashboard and switched via the
// command palette (⌘K) or the in-app sidebar menu, so the top bar stays clean.
export function Topbar() {
  const t = useTranslations();
  const theme = useTheme();
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const { clearUser } = useUser();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const toggleTheme = () =>
    theme.setTheme(theme.resolvedTheme === 'dark' ? 'light' : 'dark');

  const toggleLocale = () =>
    router.replace(pathname, { locale: locale === 'zh' ? 'en' : 'zh' });

  const handleLogout = () => {
    clearUser();
    toast.success(t('common.loggedOut'));
    router.replace('/login');
  };

  const isDark = mounted && theme.resolvedTheme === 'dark';

  return (
    <header className="topbar">
      <CommandPalette />
      <div className="topbar-right">
        <button
          className="icon-btn"
          aria-label={t('common.language')}
          onClick={toggleLocale}
          title={t('common.language')}
        >
          <IconGlobe size={16} />
        </button>
        <button
          className="icon-btn"
          aria-label={t('common.theme')}
          onClick={toggleTheme}
          title={t('common.theme')}
        >
          {isDark ? <IconMoon size={16} /> : <IconSun size={16} />}
        </button>
        <button
          className="icon-btn"
          aria-label="logout"
          onClick={handleLogout}
          title="logout"
        >
          <IconLogOut size={16} />
        </button>
      </div>
    </header>
  );
}
