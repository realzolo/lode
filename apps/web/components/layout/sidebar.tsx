'use client';

import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { Activity, Boxes, Check, GitFork, Languages, LogOut, Monitor, Moon, Search, ServerCog, Settings, Sun, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { useTheme } from 'next-themes';
import { Link, usePathname, useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';
import type { Portal } from '@/components/layout/app-shell';
import { LodeMark } from '@/components/brand/lode-logo';
import { cx } from '@/lib/cn';
import { Tooltip } from '@/components/ui/tooltip';
import type { Locale } from '@/i18n/request';

export function Sidebar({ portal, onFind, onNavigate }: { portal: Portal; onFind: () => void; onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const locale = useLocale();
  const t = useTranslations('navigation');
  const theme = useTheme();
  const { clearUser, isAdmin, user } = useUser();
  const [isApplePlatform, setIsApplePlatform] = useState(true);
  const adminNav = [
    { label: t('workspaces'), href: '/admin', icon: Boxes },
    { label: t('models'), href: '/admin/models', icon: ServerCog },
    { label: t('git'), href: '/admin/git', icon: GitFork },
    { label: t('users'), href: '/admin/users', icon: Users },
    { label: t('settings'), href: '/admin/settings', icon: Settings },
  ];
  const workbenchNav = [{ label: t('investigations'), href: '/workbench', icon: Activity }];
  const home = portal === 'admin' ? '/admin' : '/workbench';
  const nav = portal === 'admin' ? (isAdmin ? adminNav : adminNav.slice(0, 1)) : workbenchNav;
  const selectedTheme = theme.theme === 'light' || theme.theme === 'dark' || theme.theme === 'system' ? theme.theme : 'system';
  const themeItems = [
    { value: 'system', label: t('themeSystem'), icon: Monitor },
    { value: 'light', label: t('themeLight'), icon: Sun },
    { value: 'dark', label: t('themeDark'), icon: Moon },
  ] as const;
  const localeItems = [
    { value: 'zh', label: t('languageChinese') },
    { value: 'en', label: t('languageEnglish') },
  ] as const;

  useEffect(() => {
    setIsApplePlatform(/mac|iphone|ipad|ipod/i.test(navigator.userAgent));
  }, []);

  return <aside className="sidebar">
    <div className="workspace-switcher">
      <Link href={home} className="sidebar-logo" onClick={onNavigate} aria-label="Lode">
        <LodeMark className="sidebar-brand-mark" />
        <span className="sidebar-brand-name">Lode</span>
      </Link>
      <span className="workspace-plan">{portal === 'admin' ? t('controlPlane') : t('workbench')}</span>
    </div>
    <Tooltip content={t('find')} contentClassName="hidden md:block lg:hidden" side="right">
      <button className="sidebar-search" type="button" aria-label={t('find')} onClick={() => { onFind(); onNavigate?.(); }}>
        <Search size={16} aria-hidden="true" />
        <span>{t('find')}</span>
        <kbd>{t(isApplePlatform ? 'findShortcutMac' : 'findShortcutControl')}</kbd>
      </button>
    </Tooltip>
    <nav aria-label={t('primaryNavigation')} className="stack" style={{ gap: 4 }}>
      {nav.map((item) => {
        const active = pathname === item.href || (item.href !== home && pathname.startsWith(`${item.href}/`));
        return <Tooltip key={item.href} content={item.label} contentClassName="hidden md:block lg:hidden" side="right">
          <Link href={item.href} className={cx('nav-item', active && 'active')} onClick={onNavigate} aria-label={item.label}>
            <item.icon size={16} className="nav-icon" /><span className="nav-item-label">{item.label}</span>
          </Link>
        </Tooltip>;
      })}
    </nav>
    <DropdownMenu.Root>
      <Tooltip content={t('accountMenu')} contentClassName="hidden md:block lg:hidden" side="right">
        <DropdownMenu.Trigger asChild>
          <button type="button" className="sidebar-account" aria-label={t('accountMenu')}>
            <span className="account-avatar">{(user?.display_name || user?.username || 'L')[0].toUpperCase()}</span>
            <span className="account-meta"><strong>{user?.display_name || t('user')}</strong><span>{user?.username}</span></span>
          </button>
        </DropdownMenu.Trigger>
      </Tooltip>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="account-menu" side="top" align="start" sideOffset={8}>
          <DropdownMenu.Label className="account-menu-label">{t('language')}</DropdownMenu.Label>
          <DropdownMenu.RadioGroup value={locale} onValueChange={(value) => router.replace(pathname, { locale: value as Locale })} aria-label={t('language')}>
            {localeItems.map((item) => <DropdownMenu.RadioItem key={item.value} value={item.value} className="account-menu-item">
              <Languages size={15} /> {item.label}<DropdownMenu.ItemIndicator className="ml-auto"><Check className="size-4" /></DropdownMenu.ItemIndicator>
            </DropdownMenu.RadioItem>)}
          </DropdownMenu.RadioGroup>
          <DropdownMenu.Separator className="account-menu-separator" />
          <DropdownMenu.Label className="account-menu-label">{t('theme')}</DropdownMenu.Label>
          <DropdownMenu.RadioGroup value={selectedTheme} onValueChange={theme.setTheme} aria-label={t('theme')}>
            {themeItems.map((item) => <DropdownMenu.RadioItem key={item.value} value={item.value} className="account-menu-item">
              <item.icon size={15} /> {item.label}<DropdownMenu.ItemIndicator className="ml-auto"><Check className="size-4" /></DropdownMenu.ItemIndicator>
            </DropdownMenu.RadioItem>)}
          </DropdownMenu.RadioGroup>
          <DropdownMenu.Separator className="account-menu-separator" />
          <DropdownMenu.Item className="account-menu-item account-menu-item-danger" onSelect={() => { clearUser(); router.replace('/login'); }}>
            <LogOut size={15} /> {t('signOut')}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  </aside>;
}
