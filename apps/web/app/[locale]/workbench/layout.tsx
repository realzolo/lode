'use client';

// Developer Workbench shell. Any signed-in user (including admins) may use it;
// the auth cookie gate is enforced by middleware. This is the R&D surface:
// browse all analysis tasks and supplement prompts on them.

import type { ReactNode } from 'react';
import { AppShell } from '@/components/layout/app-shell';

export default function WorkbenchLayout({ children }: { children: ReactNode }) {
  return <AppShell portal="workbench">{children}</AppShell>;
}
