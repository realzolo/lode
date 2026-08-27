'use client';

import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { Activity, Boxes, Languages, LogOut, Moon, ServerCog, Settings, Sun, Users } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useTheme } from 'next-themes';
import { Link, usePathname, useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';
import type { Portal } from '@/components/layout/app-shell';
import { cx } from '@/lib/cn';

export function Sidebar({ portal, onNavigate }: { portal: Portal; onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const locale = useLocale();
  const t = useTranslations('navigation');
  const theme = useTheme();
  const { clearUser, isAdmin, user } = useUser();
  const adminNav = [
    { label: t('workspaces'), href: '/admin', icon: Boxes },
    { label: t('models'), href: '/admin/models', icon: ServerCog },
    { label: t('users'), href: '/admin/users', icon: Users },
    { label: t('settings'), href: '/admin/settings', icon: Settings },
  ];
  const workbenchNav = [{ label: t('investigations'), href: '/workbench', icon: Activity }];
  const home = portal === 'admin' ? '/admin' : '/workbench';
  const nav = portal === 'admin' ? (isAdmin ? adminNav : adminNav.slice(0, 1)) : workbenchNav;

  return <aside className="sidebar">
    <div className="workspace-switcher">
      <Link href={home} className="sidebar-logo" onClick={onNavigate}>Lode</Link>
      <span className="workspace-plan">{portal === 'admin' ? t('controlPlane') : t('workbench')}</span>
    </div>
    <nav aria-label="Primary navigation" className="stack" style={{ gap: 4 }}>
      {nav.map((item) => {
        const active = pathname === item.href || (item.href !== home && pathname.startsWith(`${item.href}/`));
        return <Link key={item.href} href={item.href} className={cx('nav-item', active && 'active')} onClick={onNavigate}>
          <item.icon size={16} className="nav-icon" />{item.label}
        </Link>;
      })}
    </nav>
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button className="sidebar-account" aria-label={t('accountMenu')}>
          <span className="account-avatar">{(user?.name || user?.email || 'L')[0].toUpperCase()}</span>
          <span className="account-meta"><strong>{user?.name || 'Lode user'}</strong><span>{user?.email}</span></span>
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="account-menu" side="top" align="start" sideOffset={8}>
          <DropdownMenu.Item className="account-menu-item" onSelect={() => router.replace(pathname, { locale: locale === 'zh' ? 'en' : 'zh' })}>
            <Languages size={15} /> {t('language')}
          </DropdownMenu.Item>
          <DropdownMenu.Item className="account-menu-item" onSelect={() => theme.setTheme(theme.resolvedTheme === 'dark' ? 'light' : 'dark')}>
            {theme.resolvedTheme === 'dark' ? <Moon size={15} /> : <Sun size={15} />} {t('theme')}
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="account-menu-separator" />
          <DropdownMenu.Item className="account-menu-item account-menu-item-danger" onSelect={() => { clearUser(); router.replace('/login'); }}>
            <LogOut size={15} /> {t('signOut')}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  </aside>;
}
