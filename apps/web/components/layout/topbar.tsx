'use client';

import { Menu } from 'lucide-react';
import { Link } from '@/lib/navigation';

export function Topbar({ portal, onMenu }: { portal: 'admin' | 'workbench'; onMenu: () => void }) {
  const controlPlane = portal === 'admin';
  return <header className="topbar">
    <div className="topbar-project">
      <button className="mobile-menu-button" aria-label="Open navigation" onClick={onMenu}><Menu size={17} /></button>
      <Link href={controlPlane ? '/admin' : '/workbench'}>Lode</Link>
    </div>
    <span className="topbar-section topbar-title">{controlPlane ? 'Control plane' : 'Investigation workbench'}</span>
    <div aria-hidden="true" />
  </header>;
}
