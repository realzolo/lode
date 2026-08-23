'use client';

import type { ReactNode } from 'react';

// Route-level enter animation for the admin portal's page content. Next.js
// re-mounts a template.tsx on every navigation, so the `.route-enter` fade-up
// (defined in globals.css) plays each time the operator moves between screens.
// The app chrome (sidebar/topbar, rendered by the parent layout) stays put.
// prefers-reduced-motion is respected via the global media query in globals.css.
export default function Template({ children }: { children: ReactNode }) {
  return <div className="route-enter">{children}</div>;
}
