'use client';

// Admin Console shell. Middleware only checks for a session cookie, so this
// layout applies the role boundary from the backend-derived user context.
// Workspace admins can use their Workspace control plane. Global provider and
// user administration remains restricted to global admins.

import { useEffect, type ReactNode } from 'react';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';
import { AppShell } from '@/components/layout/app-shell';

export default function AdminLayout({ children }: { children: ReactNode }) {
  const { isAdmin, loading, user } = useUser();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user?.must_change_password) {
      router.replace('/change-password');
    } else if (!loading && !isAdmin) {
      router.replace(user ? '/workbench' : '/login');
    }
  }, [isAdmin, loading, router, user]);

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
  if (!isAdmin || user?.must_change_password) return null;

  return <AppShell portal="admin">{children}</AppShell>;
}
