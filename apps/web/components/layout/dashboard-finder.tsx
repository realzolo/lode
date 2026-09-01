'use client';

import { useId, useMemo, useState } from 'react';
import { Activity, Boxes, GitFork, Search, ServerCog, Settings, Users } from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { Portal } from '@/components/layout/app-shell';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';

export function DashboardFinder({ portal, open, onOpenChange }: { portal: Portal; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useTranslations('navigation');
  const router = useRouter();
  const { isAdmin } = useUser();
  const resultsId = useId();
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const items = useMemo(() => portal === 'workbench'
    ? [{ label: t('investigations'), href: '/workbench', icon: Activity }]
    : [
        { label: t('workspaces'), href: '/admin', icon: Boxes },
        ...(isAdmin ? [
          { label: t('models'), href: '/admin/models', icon: ServerCog },
          { label: t('git'), href: '/admin/git', icon: GitFork },
          { label: t('users'), href: '/admin/users', icon: Users },
          { label: t('settings'), href: '/admin/settings', icon: Settings },
        ] : []),
      ], [isAdmin, portal, t]);
  const visibleItems = items.filter((item) => item.label.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));

  function navigate(href: string) {
    router.push(href);
    onOpenChange(false);
    setQuery('');
    setActiveIndex(0);
  }

  const activeItemId = visibleItems[activeIndex] ? `${resultsId}-${activeIndex}` : undefined;

  return <Dialog open={open} onOpenChange={(value) => { onOpenChange(value); if (!value) { setQuery(''); setActiveIndex(0); } }}>
    <DialogContent showClose={false} className="finder-dialog gap-0 overflow-hidden p-0">
      <DialogTitle className="sr-only">{t('find')}</DialogTitle>
      <div className="finder-input">
        <Search size={17} aria-hidden="true" />
        <Input autoFocus role="combobox" aria-label={t('find')} aria-autocomplete="list" aria-controls={resultsId} aria-expanded={open} aria-activedescendant={activeItemId} placeholder={t('findPlaceholder')} value={query} onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }} onKeyDown={(event) => {
          if (event.key === 'ArrowDown' && visibleItems.length) {
            event.preventDefault();
            setActiveIndex((current) => Math.min(current + 1, visibleItems.length - 1));
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActiveIndex((current) => Math.max(current - 1, 0));
          } else if (event.key === 'Enter' && visibleItems[activeIndex]) {
            navigate(visibleItems[activeIndex].href);
          }
        }} />
        <kbd>{t('escapeShortcut')}</kbd>
      </div>
      <p className="sr-only" aria-live="polite" aria-atomic="true">{t('findResultCount', { count: visibleItems.length })}</p>
      <div id={resultsId} className="finder-results" role="listbox" aria-label={t('findResults')}>
        {visibleItems.map((item, index) => <button id={`${resultsId}-${index}`} key={item.href} type="button" role="option" tabIndex={-1} aria-selected={activeIndex === index} onMouseMove={() => setActiveIndex(index)} onClick={() => navigate(item.href)}>
          <item.icon size={16} aria-hidden="true" /><span>{item.label}</span>
        </button>)}
        {!visibleItems.length ? <p>{t('noFindResults')}</p> : null}
      </div>
    </DialogContent>
  </Dialog>;
}
