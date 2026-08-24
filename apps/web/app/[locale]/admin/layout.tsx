'use client';

// Admin Console shell. Middleware only checks for a session cookie, so this
// layout applies the role boundary from the backend-derived user context.
// Application admins can use the application list, their own overview, and
// Members; platform-wide operational and configuration screens stay global-admin
// only.

import { useEffect, type ReactNode } from 'react';
import { usePathname, useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';
import { AppShell } from '@/components/layout/app-shell';

export default function AdminLayout({ children }: { children: ReactNode }) {
  const { isAdmin, loading, user } = useUser();
  const router = useRouter();
  const pathname = usePathname();
  const isAppAdminRoute = pathname === '/admin'
    || /^\/admin\/applications\/[^/]+(?:\/members)?\/?$/.test(pathname);

  useEffect(() => {
    if (!loading && !isAdmin && !isAppAdminRoute) {
      router.replace(user ? '/workbench' : '/login');
    }
  }, [isAdmin, isAppAdminRoute, loading, router, user]);

  if (loading) {
    // Render the chrome without children until the role is known, so we don't
    // flash a non-admin into a protected screen before the redirect fires.
    return (
      <div className="shell">
        <div className="main">
          <div className="content" />
        </div>
      </div>
    );
  }
  if (!isAdmin && !isAppAdminRoute) return null;

  return <AppShell portal="admin">{children}</AppShell>;
}
