'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/lib/navigation';
import { IconMenu } from '@/components/icons';

// The top bar only establishes the current workspace and page. Account-level
// preferences belong to the user menu at the bottom of the sidebar, matching
// the Vercel dashboard's interaction model.
export function Topbar({ portal, onMenu }: { portal: 'admin' | 'workbench'; onMenu: () => void }) {
  const t = useTranslations();
  const section = portal === 'admin' ? t('nav.applications') : t('nav.analyses');
  const homeHref = portal === 'admin' ? '/admin' : '/workbench';

  return (
    <header className="topbar">
      <div className="topbar-project">
        <button className="mobile-menu-button" aria-label="Open navigation" onClick={onMenu}><IconMenu size={17} /></button>
        <Link href={homeHref}>{portal === 'admin' ? t('nav.applications') : t('common.appName')}</Link>
      </div>
      <span className="topbar-section topbar-title">{section}</span>
      <div aria-hidden="true" />
    </header>
  );
}
