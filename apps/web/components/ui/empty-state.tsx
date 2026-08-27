import type { ReactNode } from 'react';

export function EmptyState({ icon, title, description, action }: { icon?: ReactNode; title: string; description?: string; action?: ReactNode }) {
  return <div className="flex min-h-48 flex-col items-center justify-center px-6 py-10 text-center">{icon ? <div className="mb-3 text-muted-foreground" aria-hidden="true">{icon}</div> : null}<h3 className="text-sm font-semibold">{title}</h3>{description ? <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p> : null}{action ? <div className="mt-4">{action}</div> : null}</div>;
}
