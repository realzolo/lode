'use client';

// Shared authenticated shell for both portals. Renders the portal-specific
// sidebar plus the top bar and the routed page content. The two route groups
// `admin/` and `workbench/` each wrap their pages in this shell, so the only
// difference between the two portals is the navigation surface and the access
// guard (admin requires the admin role; workbench is open to any signed-in
// user). Keeping the chrome here avoids duplicating the sidebar/topbar.

import { useEffect, useState, type ReactNode } from 'react';
import { Sidebar } from '@/components/layout/sidebar';
import { Topbar } from '@/components/layout/topbar';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';

export type Portal = 'admin' | 'workbench';

export function AppShell({ portal, children }: { portal: Portal; children: ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { user, loading } = useUser();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace('/login');
  }, [loading, router, user]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setMobileNavOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [mobileNavOpen]);

  return (
    <div className={`shell shell-${portal}`}>
      <Sidebar portal={portal} />
      <div className="main">
        <Topbar portal={portal} onMenu={() => setMobileNavOpen(true)} />
        <div className="content"><div className="page-frame">{children}</div></div>
      </div>
      <div className={`mobile-nav-drawer ${mobileNavOpen ? 'is-open' : ''}`} role="dialog" aria-label="Navigation" aria-modal="true" aria-hidden={!mobileNavOpen}>
        <button className="mobile-nav-backdrop" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)} />
        <Sidebar portal={portal} onNavigate={() => setMobileNavOpen(false)} />
      </div>
    </div>
  );
}
