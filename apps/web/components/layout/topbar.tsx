'use client';

import { Menu } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Link, usePathname } from '@/lib/navigation';
import { LodeMark } from '@/components/brand/lode-logo';
import { Tooltip } from '@/components/ui/tooltip';

export function Topbar({ portal, onMenu }: { portal: 'admin' | 'workbench'; onMenu: () => void }) {
  const t = useTranslations('navigation');
  const pathname = usePathname();
  const controlPlane = portal === 'admin';
  const title = portal === 'workbench'
    ? t('investigations')
    : pathname.startsWith('/admin/models')
      ? t('models')
      : pathname.startsWith('/admin/git')
        ? t('git')
        : pathname.startsWith('/admin/users')
          ? t('users')
          : pathname.startsWith('/admin/settings')
            ? t('settings')
            : t('workspaces');
  return <header className="topbar">
    <div className="topbar-project">
      <Tooltip content={t('openNavigation')}>
        <button type="button" className="mobile-menu-button" aria-label={t('openNavigation')} onClick={onMenu}><Menu size={17} /></button>
      </Tooltip>
      <Link href={controlPlane ? '/admin' : '/workbench'}>
        <LodeMark className="topbar-brand-mark" />
        <span>{controlPlane ? t('controlPlane') : t('workbench')}</span>
      </Link>
    </div>
    <span className="topbar-section topbar-title">{title}</span>
    <div aria-hidden="true" />
  </header>;
}
