'use client';

// Shared authenticated shell for both portals. Renders the portal-specific
// sidebar plus the top bar and the routed page content. The two route groups
// `admin/` and `workbench/` each wrap their pages in this shell, so the only
// difference between the two portals is the navigation surface and the access
// guard (admin requires the admin role; workbench is open to any signed-in
// user). Keeping the chrome here avoids duplicating the sidebar/topbar.

import type { ReactNode } from 'react';
import { Sidebar } from '@/components/layout/sidebar';
import { Topbar } from '@/components/layout/topbar';

export type Portal = 'admin' | 'workbench';

export function AppShell({ portal, children }: { portal: Portal; children: ReactNode }) {
  return (
    <div className="shell">
      <Sidebar portal={portal} />
      <div className="main">
        <Topbar />
        <div className="content">{children}</div>
      </div>
    </div>
  );
}
