'use client';

// Workbench shell for authenticated users. Administrators can use both the
// control plane and Workbench; the backend applies their unrestricted access.

import { useEffect, type ReactNode } from 'react';
import { AppShell } from '@/components/layout/app-shell';
import { PortalLoadingShell } from '@/components/layout/portal-loading-shell';
import { SessionUnavailableState } from '@/components/layout/session-unavailable-state';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';

export default function WorkbenchLayout({ children }: { children: ReactNode }) {
  const { loading, sessionError, user } = useUser();
  const router = useRouter();
  useEffect(() => {
    if (!loading && !sessionError && user?.must_change_password) router.replace('/change-password');
    else if (!loading && !sessionError && !user) router.replace('/login');
  }, [loading, router, sessionError, user]);
  if (loading) return <PortalLoadingShell />;
  if (sessionError) return <SessionUnavailableState />;
  if (!user || user.must_change_password) return null;
  return <AppShell portal="workbench">{children}</AppShell>;
}
