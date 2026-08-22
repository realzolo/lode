'use client';

import type { ReactNode } from 'react';
import { Sidebar } from '@/components/layout/sidebar';
import { Topbar } from '@/components/layout/topbar';

// Authentication is now enforced by `middleware.ts`, which reads the `lode_token`
// cookie and redirects unauthenticated requests to /login *before* the page is
// rendered. That removes the previous client-side `useEffect` token check here,
// which caused a flash-of-white on every navigation. The signed-in user is
// provided by <UserProvider> (seeded from /auth/me) for the sidebar/topbar.
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <Topbar />
        <div className="content">{children}</div>
      </div>
    </div>
  );
}
