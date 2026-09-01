'use client';

// Shared authenticated shell for both portals. Renders the portal-specific
// sidebar plus the top bar and the routed page content. The two route groups
// `admin/` and `workbench/` each wrap their pages in this shell, so the only
// difference between the two portals is the navigation surface and the access
// guard (admin requires the admin role; workbench is open to any signed-in
// user). Keeping the chrome here avoids duplicating the sidebar/topbar.

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslations } from 'next-intl';
import { Sidebar } from '@/components/layout/sidebar';
import { Topbar } from '@/components/layout/topbar';
import { DashboardFinder } from '@/components/layout/dashboard-finder';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';

export type Portal = 'admin' | 'workbench';

const drawerFocusableSelector = [
  '.sidebar a[href]',
  '.sidebar button:not([disabled])',
  '.sidebar [tabindex]:not([tabindex="-1"])',
].join(',');

function drawerFocusableElements(drawer: HTMLElement) {
  return Array.from(drawer.querySelectorAll<HTMLElement>(drawerFocusableSelector)).filter((element) => {
    const style = window.getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  });
}

export function AppShell({ portal, children }: { portal: Portal; children: ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [finderOpen, setFinderOpen] = useState(false);
  const mobileNavRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const t = useTranslations('navigation');
  const { user, loading } = useUser();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace('/login');
  }, [loading, router, user]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const drawer = mobileNavRef.current;
    const focusFirst = () => drawer && drawerFocusableElements(drawer)[0]?.focus();
    const frame = window.requestAnimationFrame(focusFirst);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setMobileNavOpen(false);
        return;
      }
      if (event.key !== 'Tab' || !drawer) return;
      const focusable = drawerFocusableElements(drawer);
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    };
  }, [mobileNavOpen]);

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editingControl = target?.closest('input, textarea, select, [role="textbox"], [role="combobox"], [contenteditable]:not([contenteditable="false"])');
      if (event.key.toLowerCase() !== 'k' || (!event.metaKey && !event.ctrlKey) || event.altKey || editingControl) return;
      event.preventDefault();
      setMobileNavOpen(false);
      setFinderOpen(true);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    <div className={`shell shell-${portal}`}>
      <Sidebar portal={portal} onFind={() => setFinderOpen(true)} />
      <div className="main">
        <Topbar portal={portal} onMenu={() => setMobileNavOpen(true)} />
        <div className="content"><div className="page-frame">{children}</div></div>
      </div>
      <div ref={mobileNavRef} className={`mobile-nav-drawer ${mobileNavOpen ? 'is-open' : ''}`} role="dialog" aria-label={t('navigation')} aria-modal="true" aria-hidden={!mobileNavOpen}>
        <button type="button" className="mobile-nav-backdrop" aria-label={t('closeNavigation')} onClick={() => setMobileNavOpen(false)} />
        <Sidebar portal={portal} onFind={() => setFinderOpen(true)} onNavigate={() => setMobileNavOpen(false)} />
      </div>
      <DashboardFinder portal={portal} open={finderOpen} onOpenChange={setFinderOpen} />
    </div>
  );
}
