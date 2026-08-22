'use client';

// Admin Console shell. Enforces the admin role on the client: middleware only
// checks that a session cookie is present, so the role gate lives here using the
// backend-derived user from <UserProvider>. Non-admins are bounced to the
// developer workbench.

import { useEffect, type ReactNode } from 'react';
import { useRouter } from '@/lib/navigation';
import { useUser } from '@/lib/user-context';
import { AppShell } from '@/components/layout/app-shell';

export default function AdminLayout({ children }: { children: ReactNode }) {
  const { isAdmin, loading } = useUser();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !isAdmin) router.replace('/workbench');
  }, [isAdmin, loading, router]);

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
  if (!isAdmin) return null;

  return <AppShell portal="admin">{children}</AppShell>;
}
