'use client';

// Admin Console shell. Middleware only checks for a session cookie, so this
// layout applies the role boundary from the backend-derived user context.
// Workspace admins can use their Workspace control plane. Global provider and
// user administration remains restricted to global admins.

import { useEffect, type ReactNode } from 'react';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';
import { AppShell } from '@/components/layout/app-shell';
import { PortalLoadingShell } from '@/components/layout/portal-loading-shell';
import { SessionUnavailableState } from '@/components/layout/session-unavailable-state';

export default function AdminLayout({ children }: { children: ReactNode }) {
  const { isAdmin, loading, sessionError, user } = useUser();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !sessionError && user?.must_change_password) {
      router.replace('/change-password');
    } else if (!loading && !sessionError && !isAdmin) {
      router.replace(user ? '/workbench' : '/login');
    }
  }, [isAdmin, loading, router, sessionError, user]);

  if (loading) return <PortalLoadingShell />;
  if (sessionError) return <SessionUnavailableState />;
  if (!isAdmin || user?.must_change_password) return null;

  return <AppShell portal="admin">{children}</AppShell>;
}
