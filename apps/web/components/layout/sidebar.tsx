'use client';

import { useEffect, useState } from 'react';
import { Link, usePathname } from '@/lib/navigation';
import { useTranslations } from 'next-intl';
import { cx } from '@/lib/cn';
import { useUser } from '@/lib/user-context';
import { fetchApplications } from '@/lib/api';
import type { Application } from '@/lib/types';
import {
  IconHome,
  IconBarChart,
  IconDatabase,
  IconSettings,
  IconUsers,
  IconChevronLeft,
} from '@/components/icons';

const GLOBAL_NAV = [
  { key: 'dashboard', href: '/dashboard', adminOnly: false, Icon: IconHome },
  { key: 'analyses', href: '/analyses', adminOnly: false, Icon: IconBarChart },
  { key: 'memories', href: '/memories', adminOnly: false, Icon: IconDatabase },
  { key: 'settings', href: '/settings', adminOnly: false, Icon: IconSettings },
  { key: 'users', href: '/users', adminOnly: true, Icon: IconUsers },
] as const;

// Second-level menu shown when inside an application (`/applications/:id`).
// Mirrors Vercel's project sidebar: a parent label + nested, indented items.
const APP_SUBNAV = [
  { key: 'overview', suffix: '' },
  { key: 'repositories', suffix: 'repos' },
  { key: 'prompts', suffix: 'prompts' },
  { key: 'dataSources', suffix: 'db' },
  { key: 'model', suffix: 'model' },
] as const;

export function Sidebar() {
  const t = useTranslations('nav');
  const pathname = usePathname();
  const { isAdmin } = useUser();
  const [apps, setApps] = useState<Application[]>([]);

  const appMatch = pathname.match(/^\/applications\/([^/]+)/);
  const appId = appMatch ? appMatch[1] : null;

  useEffect(() => {
    fetchApplications()
      .then(setApps)
      .catch(() => setApps([]));
  }, []);

  const currentApp = appId ? apps.find((a) => String(a.id) === appId) : undefined;

  const renderGlobal = (omitDashboard = false) =>
    GLOBAL_NAV.map((item) => {
      if (item.adminOnly && !isAdmin) return null;
      if (omitDashboard && item.key === 'dashboard') return null;
      const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
      return (
        <Link
          key={item.key}
          href={item.href}
          className={cx('nav-item', active && 'active')}
        >
          <item.Icon size={16} className="nav-icon" />
          {t(item.key)}
        </Link>
      );
    });

  return (
    <aside className="sidebar">
      <Link href="/dashboard" className="sidebar-logo">
        Incident Trace
      </Link>

      {appId ? (
        <>
          <Link href="/dashboard" className="nav-item nav-back">
            <IconChevronLeft size={16} className="nav-icon" />
            {t('allApplications')}
          </Link>
          <div className="sidebar-label">{currentApp?.name ?? t('applications')}</div>
          {APP_SUBNAV.map((sub) => {
            const href = sub.suffix
              ? `/applications/${appId}/${sub.suffix}`
              : `/applications/${appId}`;
            const active = sub.suffix
              ? pathname === href
              : pathname === `/applications/${appId}`;
            return (
              <Link
                key={sub.key}
                href={href}
                className={cx('nav-item nav-subitem', active && 'active')}
              >
                {t(sub.key)}
              </Link>
            );
          })}
          <div className="sidebar-divider" />
          {renderGlobal(true)}
        </>
      ) : (
        renderGlobal(false)
      )}
    </aside>
  );
}
