'use client';

// Workbench shell for authenticated users. Administrators can use both the
// control plane and Workbench; the backend applies their unrestricted access.

import { useEffect, type ReactNode } from 'react';
import { AppShell } from '@/components/layout/app-shell';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';

export default function WorkbenchLayout({ children }: { children: ReactNode }) {
  const { loading, user } = useUser();
  const router = useRouter();
  useEffect(() => {
    if (!loading && user?.must_change_password) router.replace('/change-password');
    else if (!loading && !user) router.replace('/login');
  }, [loading, router, user]);
  if (loading || !user || user.must_change_password) return null;
  return <AppShell portal="workbench">{children}</AppShell>;
}
