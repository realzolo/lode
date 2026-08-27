'use client';

// Workbench shell for ordinary users only. The backend enforces the same split
// so an administrator cannot access investigation data with a crafted URL.

import { useEffect, type ReactNode } from 'react';
import { AppShell } from '@/components/layout/app-shell';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';

export default function WorkbenchLayout({ children }: { children: ReactNode }) {
  const { isAdmin, loading, user } = useUser();
  const router = useRouter();
  useEffect(() => {
    if (!loading && user?.must_change_password) router.replace('/change-password');
    else if (!loading && isAdmin) router.replace('/admin');
    else if (!loading && !user) router.replace('/login');
  }, [isAdmin, loading, router, user]);
  if (loading || isAdmin || !user || user.must_change_password) return null;
  return <AppShell portal="workbench">{children}</AppShell>;
}
