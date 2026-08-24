'use client';

import { useEffect, useState } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { Link, usePathname, useRouter } from '@/lib/navigation';
import { useLocale, useTranslations } from 'next-intl';
import { useTheme } from 'next-themes';
import { toast } from 'sonner';
import { cx } from '@/lib/cn';
import { useUser } from '@/lib/user-context';
import { fetchApplications } from '@/lib/api';
import type { Application } from '@/lib/types';
import type { Portal } from '@/components/layout/app-shell';
import {
  IconHome,
  IconBarChart,
  IconDatabase,
  IconSettings,
  IconUsers,
  IconChevronLeft,
  IconTerminal,
  IconSearch,
  IconShield,
  IconAlertTriangle,
  IconGlobe,
  IconLogOut,
  IconMoon,
  IconMoreVertical,
  IconSun,
} from '@/components/icons';

// Each portal exposes a distinct navigation surface so admin management and the
// developer workbench never share a menu. The workbench deliberately omits any
// management entry (settings / users / app editing) — those live only in the
// admin console.

const ADMIN_NAV = [
  { key: 'applications', href: '/admin', Icon: IconHome },
  { key: 'settings', href: '/admin/settings', Icon: IconSettings },
  { key: 'users', href: '/admin/users', Icon: IconUsers },
  { key: 'experiences', href: '/admin/experiences', Icon: IconDatabase },
  { key: 'audit', href: '/admin/audit', Icon: IconShield },
  { key: 'deadLetters', href: '/admin/dead-letters', Icon: IconAlertTriangle },
] as const;

const WORKBENCH_NAV = [
  { key: 'analyses', href: '/workbench', Icon: IconBarChart },
  { key: 'experiences', href: '/workbench/experiences', Icon: IconDatabase },
  { key: 'explore', href: '/workbench/explore', Icon: IconTerminal },
] as const;

// Second-level menu shown when inside an application (`/admin/applications/:id`).
// Mirrors Vercel's project sidebar: a parent label + nested, indented items.
const ADMIN_APP_SUBNAV = [
  { key: 'overview', suffix: '' },
  { key: 'repositories', suffix: 'repos' },
  { key: 'descriptions', suffix: 'descriptions' },
  { key: 'dataSources', suffix: 'db' },
  { key: 'model', suffix: 'model' },
  { key: 'experiences', suffix: 'experiences' },
  { key: 'members', suffix: 'members' },
] as const;

export function Sidebar({ portal, onNavigate }: { portal: Portal; onNavigate?: () => void }) {
  const t = useTranslations('nav');
  const tc = useTranslations('common');
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const [apps, setApps] = useState<Application[]>([]);
  const theme = useTheme();
  const { clearUser, isAdmin, user } = useUser();

  const homeHref = portal === 'admin' ? '/admin' : '/workbench';

  // The app subnav is only relevant inside the admin console's application
  // settings, so it is parsed from the admin path only.
  const appMatch = portal === 'admin' ? pathname.match(/^\/admin\/applications\/([^/]+)/) : null;
  const appId = appMatch ? appMatch[1] : null;

  useEffect(() => {
    if (portal !== 'admin') return;
    fetchApplications()
      .then(setApps)
      .catch(() => setApps([]));
  }, [portal]);

  const currentApp = appId ? apps.find((a) => String(a.id) === appId) : undefined;

  const nav = portal === 'admin'
    ? (isAdmin ? ADMIN_NAV : ADMIN_NAV.slice(0, 1))
    : WORKBENCH_NAV;

  const toggleTheme = () => {
    theme.setTheme(theme.resolvedTheme === 'dark' ? 'light' : 'dark');
  };

  const toggleLocale = () => {
    router.replace(pathname, { locale: locale === 'zh' ? 'en' : 'zh' });
  };

  const handleLogout = () => {
    clearUser();
    toast.success(tc('loggedOut'));
    router.replace('/login');
  };

  const renderNav = () =>
    nav.map((item) => {
      // The home entry (`/admin`) must not also match `/admin/settings` etc., so
      // we only allow prefix matching for non-home items.
      const active =
        pathname === item.href ||
        (item.href !== homeHref && pathname.startsWith(`${item.href}/`));
      return (
        <Link key={item.key} href={item.href} className={cx('nav-item', active && 'active')} onClick={onNavigate}>
          <item.Icon size={16} className="nav-icon" />
          {t(item.key)}
        </Link>
      );
    });

  return (
    <aside className="sidebar">
      <div className="workspace-switcher">
        <Link href={homeHref} className="sidebar-logo" onClick={onNavigate}>
          Lode
        </Link>
        <span className="workspace-plan">{portal === 'admin' ? 'Admin' : 'Workspace'}</span>
      </div>

      <button
        className="sidebar-search"
        aria-label={tc('search')}
        onClick={() => {
          onNavigate?.();
          window.dispatchEvent(new Event('lode:open-command-palette'));
        }}
      >
        <IconSearch size={17} className="nav-icon" />
        <span>{tc('search')}</span>
        <kbd>F</kbd>
      </button>

      {portal === 'admin' && appId ? (
        <>
          <Link href="/admin" className="nav-item nav-back" onClick={onNavigate}>
            <IconChevronLeft size={16} className="nav-icon" />
            {t('allApplications')}
          </Link>
          <div className="sidebar-label" title={currentApp?.name ?? undefined}>
            {currentApp?.name ?? t('applications')}
          </div>
          {(isAdmin ? ADMIN_APP_SUBNAV : ADMIN_APP_SUBNAV.filter((sub) => sub.key === 'overview' || sub.key === 'members')).map((sub) => {
            const href = sub.suffix
              ? `/admin/applications/${appId}/${sub.suffix}`
              : `/admin/applications/${appId}`;
            const active = sub.suffix
              ? pathname === href
              : pathname === `/admin/applications/${appId}`;
            return (
              <Link key={sub.key} href={href} className={cx('nav-item nav-subitem', active && 'active')} onClick={onNavigate}>
                {t(sub.key)}
              </Link>
            );
          })}
        </>
      ) : (
        renderNav()
      )}

      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button className="sidebar-account" aria-label={user?.name || user?.email || 'Account menu'}>
            <span className="account-avatar" aria-hidden="true">{(user?.name || user?.email || 'L').charAt(0).toUpperCase()}</span>
            <span className="account-meta"><strong>{user?.name || 'Lode user'}</strong><span>{user?.email || 'Signed in'}</span></span>
            <IconMoreVertical className="sidebar-account-more" size={16} aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content className="account-menu" side="top" align="start" sideOffset={8}>
            <div className="account-menu-header"><strong>{user?.name || 'Lode user'}</strong><span>{user?.email || 'Signed in'}</span></div>
            <DropdownMenu.Separator className="account-menu-separator" />
            <DropdownMenu.Item className="account-menu-item" onSelect={toggleLocale}>
              <IconGlobe size={15} />{tc('language')}
            </DropdownMenu.Item>
            <DropdownMenu.Item className="account-menu-item" onSelect={toggleTheme}>
              {theme.resolvedTheme === 'dark' ? <IconMoon size={15} /> : <IconSun size={15} />}{tc('theme')}
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="account-menu-separator" />
            <DropdownMenu.Item className="account-menu-item account-menu-item-danger" onSelect={handleLogout}>
              <IconLogOut size={15} />{tc('logout')}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </aside>
  );
}
