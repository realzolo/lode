'use client';

import { useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useRouter, usePathname } from '@/lib/navigation';
import { useTranslations } from 'next-intl';
import { IconSearch } from '@/components/icons';
import { fetchInvestigations, fetchApplications, type InvestigationSummary } from '@/lib/api';
import { useUser } from '@/lib/user-context';
import type { Application } from '@/lib/types';

// Command palette (⌘K). Scoped to the active portal so it never offers a route
// the user can't reach: the admin console lists management screens + apps, the
// workbench lists investigation surfaces. This keeps the two ends from leaking into
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
  { id: 'audit', key: 'nav.audit', href: '/admin/audit' },
  { id: 'dead-letters', key: 'nav.deadLetters', href: '/admin/dead-letters' },
];

const WORKBENCH_NAV: { id: string; key: string; href: string }[] = [
  { id: 'investigations', key: 'nav.investigations', href: '/workbench' },
];

export function CommandPalette({ showTrigger = true }: { showTrigger?: boolean }) {
  const t = useTranslations();
  const router = useRouter();
  const pathname = usePathname();
  const { isAdmin } = useUser();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [apps, setApps] = useState<Application[]>([]);
  const [investigations, setInvestigations] = useState<InvestigationSummary[]>([]);

  const portal = pathname.startsWith('/admin') ? 'admin' : 'workbench';

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const key = typeof e.key === 'string' ? e.key.toLowerCase() : '';
      if ((e.metaKey || e.ctrlKey) && key === 'k') {
        e.preventDefault();
        setOpen((o) => !o);
      }
      const isTyping = e.target instanceof HTMLElement && e.target.matches('input, textarea, select, [contenteditable="true"]');
      if (!e.metaKey && !e.ctrlKey && !e.altKey && key === 'f' && !isTyping) {
        e.preventDefault();
        setOpen(true);
      }
      if (e.key === 'Escape') setOpen(false);
    };
    const openPalette = () => setOpen(true);
    window.addEventListener('keydown', onKey);
    window.addEventListener('lode:open-command-palette', openPalette);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('lode:open-command-palette', openPalette);
    };
  }, []);

  useEffect(() => {
    let active = true;
    if (portal === 'admin') {
      fetchApplications().then((data) => active && setApps(data)).catch(() => active && setApps([]));
      setInvestigations([]);
    } else {
      fetchInvestigations().then((data) => active && setInvestigations(data)).catch(() => active && setInvestigations([]));
      setApps([]);
    }
    return () => {
      active = false;
    };
  }, [portal]);

  const go = (path: string) => {
    router.push(path);
    setOpen(false);
    setQuery('');
    setActiveIndex(0);
  };

  const navCommands: Command[] = (portal === 'admin'
    ? (isAdmin ? ADMIN_NAV : ADMIN_NAV.slice(0, 1))
    : WORKBENCH_NAV).map((n) => ({
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

  const investigationCommands: Command[] =
    portal === 'workbench'
      ? investigations.map((investigation) => ({
          id: `investigation-${investigation.id}`,
          label: `${investigation.title || investigation.id} · ${investigation.status}`,
          run: () => go(`/workbench/investigation/${investigation.id}`),
        }))
      : [];

  const q = query.toLowerCase();
  const filteredNav = navCommands.filter((c) => c.label.toLowerCase().includes(q));
  const filteredApps = appCommands.filter((c) => c.label.toLowerCase().includes(q));
  const filteredInvestigations = investigationCommands.filter((c) => c.label.toLowerCase().includes(q));
  const visibleCommands = useMemo(
    () => [...filteredNav, ...filteredApps, ...filteredInvestigations],
    [filteredInvestigations, filteredApps, filteredNav],
  );

  useEffect(() => {
    setActiveIndex(0);
  }, [query, portal]);

  const commandIndex = (command: Command) => visibleCommands.findIndex((candidate) => candidate.id === command.id);

  const onInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' && visibleCommands.length) {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % visibleCommands.length);
    } else if (event.key === 'ArrowUp' && visibleCommands.length) {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + visibleCommands.length) % visibleCommands.length);
    } else if (event.key === 'Enter') {
      const command = visibleCommands[activeIndex];
      if (command) {
        event.preventDefault();
        command.run();
      }
    }
  };

  if (!open) {
    if (!showTrigger) return null;
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
      <div className="cmdk-panel" role="dialog" aria-modal="true" aria-label={t('common.command')} onClick={(e) => e.stopPropagation()}>
        <input
          autoFocus
          className="input"
          role="combobox"
          aria-expanded="true"
          aria-controls="command-results"
          aria-activedescendant={visibleCommands[activeIndex] ? `command-${visibleCommands[activeIndex].id}` : undefined}
          placeholder={t('common.search')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onInputKeyDown}
        />
        <div id="command-results" className="cmdk-list" role="listbox">
          {filteredNav.length === 0 && filteredApps.length === 0 && filteredInvestigations.length === 0 && (
            <div className="cmdk-empty">{t('common.empty')}</div>
          )}
          {filteredNav.map((c) => (
            <button key={c.id} id={`command-${c.id}`} role="option" aria-selected={activeIndex === commandIndex(c)} className="cmdk-item" data-active={activeIndex === commandIndex(c) ? 'true' : undefined} onMouseEnter={() => setActiveIndex(commandIndex(c))} onClick={c.run}>
              {c.label}
            </button>
          ))}
          {filteredApps.length > 0 && (
            <>
              <div className="cmdk-group">{t('nav.applications')}</div>
              {filteredApps.map((c) => (
                <button key={c.id} id={`command-${c.id}`} role="option" aria-selected={activeIndex === commandIndex(c)} className="cmdk-item" data-active={activeIndex === commandIndex(c) ? 'true' : undefined} onMouseEnter={() => setActiveIndex(commandIndex(c))} onClick={c.run}>
                  {c.label}
                </button>
              ))}
            </>
          )}
          {filteredInvestigations.length > 0 && (
            <>
              <div className="cmdk-group">调查</div>
              {filteredInvestigations.map((c) => (
                <button key={c.id} id={`command-${c.id}`} role="option" aria-selected={activeIndex === commandIndex(c)} className="cmdk-item" data-active={activeIndex === commandIndex(c) ? 'true' : undefined} onMouseEnter={() => setActiveIndex(commandIndex(c))} onClick={c.run}>
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
