'use client';

import { Link, usePathname } from '@/lib/navigation';
import { useTranslations } from 'next-intl';
import { cx } from '@/lib/cn';
import { useUser } from '@/lib/user-context';
import {
  IconHome,
  IconBarChart,
  IconDatabase,
  IconSettings,
  IconUsers,
} from '@/components/icons';

const NAV = [
  { key: 'dashboard', href: '/dashboard', adminOnly: false, Icon: IconHome },
  { key: 'analyses', href: '/analyses', adminOnly: false, Icon: IconBarChart },
  { key: 'memories', href: '/memories', adminOnly: false, Icon: IconDatabase },
  { key: 'settings', href: '/settings', adminOnly: false, Icon: IconSettings },
  { key: 'users', href: '/users', adminOnly: true, Icon: IconUsers },
] as const;

export function Sidebar() {
  const t = useTranslations('nav');
  const pathname = usePathname();
  const { isAdmin } = useUser();

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">Incident Trace</div>
      {NAV.map((item) => {
        if (item.adminOnly && !isAdmin) return null;
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
      })}
    </aside>
  );
}
