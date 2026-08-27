'use client';

import { Menu } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Link } from '@/lib/navigation';

export function Topbar({ portal, onMenu }: { portal: 'admin' | 'workbench'; onMenu: () => void }) {
  const t = useTranslations('navigation');
  const controlPlane = portal === 'admin';
  return <header className="topbar">
    <div className="topbar-project">
      <button className="mobile-menu-button" aria-label={t('accountMenu')} onClick={onMenu}><Menu size={17} /></button>
      <Link href={controlPlane ? '/admin' : '/workbench'}>Lode</Link>
    </div>
    <span className="topbar-section topbar-title">{controlPlane ? t('controlPlane') : t('workbench')}</span>
    <div aria-hidden="true" />
  </header>;
}
