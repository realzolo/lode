'use client';

import { useEffect, useState } from 'react';
import { useRouter, usePathname } from '@/lib/navigation';
import { useTranslations } from 'next-intl';
import { IconSearch } from '@/components/icons';
import { fetchApplications } from '@/lib/api';
import type { Application } from '@/lib/types';

// Command palette (⌘K). Scoped to the active portal so it never offers a route
// the user can't reach: the admin console lists management screens + apps, the
// workbench lists analysis surfaces. This keeps the two ends from leaking into
// each other even through the search shortcut.

interface Command {
  id: string;
  label: string;
  run: () => void;
}

const ADMIN_NAV: { id: string; key: string; href: string }[] = [
  { id: 'applications', key: 'nav.applications', href: '/admin' },
  { id: 'settings', key: 'nav.settings', href: '/admin/settings' },
  { id: 'users', key: 'nav.users', href: '/admin/users' },
  { id: 'memories', key: 'nav.memories', href: '/admin/memories' },
];

const WORKBENCH_NAV: { id: string; key: string; href: string }[] = [
  { id: 'analyses', key: 'nav.analyses', href: '/workbench' },
  { id: 'memories', key: 'nav.memories', href: '/workbench/memories' },
];

export function CommandPalette() {
  const t = useTranslations();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [apps, setApps] = useState<Application[]>([]);

  const portal = pathname.startsWith('/admin') ? 'admin' : 'workbench';

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((o) => !o);
      }
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (portal !== 'admin') return;
    fetchApplications()
      .then(setApps)
      .catch(() => setApps([]));
  }, [portal]);

  const go = (path: string) => {
    router.push(path);
    setOpen(false);
    setQuery('');
  };

  const navCommands: Command[] = (portal === 'admin' ? ADMIN_NAV : WORKBENCH_NAV).map((n) => ({
    id: n.id,
    label: t(n.key),
    run: () => go(n.href),
  }));

  const appCommands: Command[] =
    portal === 'admin'
      ? apps.map((a) => ({
          id: `app-${a.id}`,
          label: a.name,
          run: () => go(`/admin/applications/${a.id}`),
        }))
      : [];

  const q = query.toLowerCase();
  const filteredNav = navCommands.filter((c) => c.label.toLowerCase().includes(q));
  const filteredApps = appCommands.filter((c) => c.label.toLowerCase().includes(q));

  if (!open) {
    return (
      <button className="cmdk" onClick={() => setOpen(true)} aria-label={t('common.command')}>
        <IconSearch size={16} />
        <span className="mono">⌘K</span>
        <span>{t('common.search')}</span>
      </button>
    );
  }

  return (
    <div className="cmdk-overlay" onClick={() => setOpen(false)}>
      <div className="cmdk-panel" onClick={(e) => e.stopPropagation()}>
        <input
          autoFocus
          className="input"
          placeholder={t('common.search')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="cmdk-list">
          {filteredNav.length === 0 && filteredApps.length === 0 && (
            <div className="cmdk-empty">{t('common.empty')}</div>
          )}
          {filteredNav.map((c) => (
            <button key={c.id} className="cmdk-item" onClick={c.run}>
              {c.label}
            </button>
          ))}
          {filteredApps.length > 0 && (
            <>
              <div className="cmdk-group">{t('nav.applications')}</div>
              {filteredApps.map((c) => (
                <button key={c.id} className="cmdk-item" onClick={c.run}>
                  {c.label}
                </button>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
