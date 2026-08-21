'use client';

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useRouter } from '@/lib/navigation';
import { fetchCurrentUser, getToken, setRole } from '@/lib/api';
import { Sidebar } from '@/components/layout/sidebar';
import { Topbar } from '@/components/layout/topbar';

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace('/login');
      return;
    }
    setChecked(true);
    // Keep the cached role fresh so the admin-only nav appears correctly.
    fetchCurrentUser()
      .then((u) => setRole(u.role))
      .catch(() => {});
  }, [router]);

  if (!checked) {
    return null;
  }

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
