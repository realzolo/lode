'use client';

import { Link, usePathname } from '@/lib/navigation';
import { useTranslations } from 'next-intl';
import { cx } from '@/lib/cn';
import { useUser } from '@/lib/user-context';

const NAV = [
  { key: 'dashboard', href: '/dashboard', adminOnly: false },
  { key: 'analyses', href: '/analyses', adminOnly: false },
  { key: 'memories', href: '/memories', adminOnly: false },
  { key: 'settings', href: '/settings', adminOnly: false },
  { key: 'users', href: '/users', adminOnly: true },
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
            {t(item.key)}
          </Link>
        );
      })}
    </aside>
  );
}
