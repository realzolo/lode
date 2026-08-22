'use client';

import { useEffect, useState } from 'react';
import { Link, usePathname } from '@/lib/navigation';
import { useTranslations } from 'next-intl';
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
} from '@/components/icons';

// Each portal exposes a distinct navigation surface so admin management and the
// developer workbench never share a menu. The workbench deliberately omits any
// management entry (settings / users / app editing) — those live only in the
// admin console.

const ADMIN_NAV = [
  { key: 'applications', href: '/admin', Icon: IconHome },
  { key: 'settings', href: '/admin/settings', Icon: IconSettings },
  { key: 'users', href: '/admin/users', Icon: IconUsers },
  { key: 'memories', href: '/admin/memories', Icon: IconDatabase },
] as const;

const WORKBENCH_NAV = [
  { key: 'analyses', href: '/workbench', Icon: IconBarChart },
  { key: 'memories', href: '/workbench/memories', Icon: IconDatabase },
] as const;

// Second-level menu shown when inside an application (`/admin/applications/:id`).
// Mirrors Vercel's project sidebar: a parent label + nested, indented items.
const ADMIN_APP_SUBNAV = [
  { key: 'overview', suffix: '' },
  { key: 'repositories', suffix: 'repos' },
  { key: 'prompts', suffix: 'prompts' },
  { key: 'dataSources', suffix: 'db' },
  { key: 'model', suffix: 'model' },
  { key: 'memories', suffix: 'memories' },
  { key: 'members', suffix: 'members' },
] as const;

export function Sidebar({ portal }: { portal: Portal }) {
  const t = useTranslations('nav');
  const pathname = usePathname();
  const [apps, setApps] = useState<Application[]>([]);

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

  const nav = portal === 'admin' ? ADMIN_NAV : WORKBENCH_NAV;

  const renderNav = () =>
    nav.map((item) => {
      // The home entry (`/admin`) must not also match `/admin/settings` etc., so
      // we only allow prefix matching for non-home items.
      const active =
        pathname === item.href ||
        (item.href !== homeHref && pathname.startsWith(`${item.href}/`));
      return (
        <Link key={item.key} href={item.href} className={cx('nav-item', active && 'active')}>
          <item.Icon size={16} className="nav-icon" />
          {t(item.key)}
        </Link>
      );
    });

  return (
    <aside className="sidebar">
      <Link href={homeHref} className="sidebar-logo">
        Lode
      </Link>

      {portal === 'admin' && appId ? (
        <>
          <Link href="/admin" className="nav-item nav-back">
            <IconChevronLeft size={16} className="nav-icon" />
            {t('allApplications')}
          </Link>
          <div className="sidebar-label" title={currentApp?.name ?? undefined}>
            {currentApp?.name ?? t('applications')}
          </div>
          {ADMIN_APP_SUBNAV.map((sub) => {
            const href = sub.suffix
              ? `/admin/applications/${appId}/${sub.suffix}`
              : `/admin/applications/${appId}`;
            const active = sub.suffix
              ? pathname === href
              : pathname === `/admin/applications/${appId}`;
            return (
              <Link key={sub.key} href={href} className={cx('nav-item nav-subitem', active && 'active')}>
                {t(sub.key)}
              </Link>
            );
          })}
          <div className="sidebar-divider" />
          {renderNav()}
        </>
      ) : (
        renderNav()
      )}
    </aside>
  );
}
