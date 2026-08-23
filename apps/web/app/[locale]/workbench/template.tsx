'use client';

import type { ReactNode } from 'react';

// Route-level enter animation for the workbench portal's page content. See the
// admin template for rationale; this mirrors it for the developer-facing portal.
export default function Template({ children }: { children: ReactNode }) {
  return <div className="route-enter">{children}</div>;
}
