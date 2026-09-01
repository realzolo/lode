import type { ReactNode } from 'react';

export interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  compact?: boolean;
}

export function EmptyState({ icon, title, description, action, compact = false }: EmptyStateProps) {
  return <div className={`empty-state-content flex flex-col items-center justify-center text-center ${compact ? 'min-h-24 px-4 py-6' : 'min-h-48 px-6 py-10'}`}>{icon ? <div className="mb-3 text-muted-foreground" aria-hidden="true">{icon}</div> : null}<h3 className="text-sm font-semibold">{title}</h3>{description ? <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p> : null}{action ? <div className="mt-4">{action}</div> : null}</div>;
}

// Geist tables hand an empty collection off to a separate state, rather than
// retaining an otherwise meaningless header-only table.
export function TableEmptyState(props: EmptyStateProps) {
  return <section className="dashboard-empty" aria-live="polite" aria-atomic="true"><EmptyState {...props} /></section>;
}
