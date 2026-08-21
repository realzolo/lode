'use client';

import { Link, usePathname } from '@/lib/navigation';
import { useTranslations } from 'next-intl';
import { cx } from '@/lib/cn';

const NAV = [
  { key: 'dashboard', href: '/dashboard' },
  { key: 'analyses', href: '/analyses' },
  { key: 'memories', href: '/memories' },
  { key: 'settings', href: '/settings' },
] as const;

export function Sidebar() {
  const t = useTranslations('nav');
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">Incident Trace</div>
      {NAV.map((item) => {
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
