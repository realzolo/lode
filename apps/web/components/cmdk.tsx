'use client';

import { useEffect, useState } from 'react';
import { useRouter } from '@/lib/navigation';
import { useTranslations } from 'next-intl';
import { cx } from '@/lib/cn';

interface Command {
  id: string;
  label: string;
  run: () => void;
}

export function CommandPalette() {
  const t = useTranslations();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

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

  const go = (path: string) => {
    router.push(path);
    setOpen(false);
    setQuery('');
  };

  const commands: Command[] = [
    { id: 'dashboard', label: t('nav.dashboard'), run: () => go('/dashboard') },
    { id: 'analyses', label: t('nav.analyses'), run: () => go('/analyses') },
    { id: 'memories', label: t('nav.memories'), run: () => go('/memories') },
    { id: 'settings', label: t('nav.settings'), run: () => go('/settings') },
  ];

  const filtered = commands.filter((c) =>
    c.label.toLowerCase().includes(query.toLowerCase()),
  );

  if (!open) {
    return (
      <button className="cmdk" onClick={() => setOpen(true)} aria-label={t('common.command')}>
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
          {filtered.length === 0 && <div className="cmdk-empty">{t('common.empty')}</div>}
          {filtered.map((c) => (
            <button key={c.id} className="cmdk-item" onClick={c.run}>
              {c.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
