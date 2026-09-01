import { Skeleton } from '@/components/ui/skeleton';

export function ListSkeleton({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return <div className="list-skeleton" aria-busy="true">{Array.from({ length: rows }, (_, row) => <div key={row} className="list-skeleton-row" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>{Array.from({ length: columns }, (_, column) => <Skeleton key={column} className={column === 0 ? 'h-4 w-3/4' : 'h-3 w-2/3'} />)}</div>)}</div>;
}
