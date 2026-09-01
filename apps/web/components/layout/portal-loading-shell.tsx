import { Skeleton } from '@/components/ui/skeleton';
import { ListSkeleton } from '@/components/ui/list-skeleton';

// Keep the authenticated route boundary visible while /auth/me establishes the
// session. The skeletons are decorative; aria-busy is announced by the shell.
export function PortalLoadingShell() {
  return (
    <div className="shell portal-loading-shell" aria-busy="true">
      <aside className="sidebar portal-loading-sidebar">
        <div className="flex h-10 items-center px-2">
          <Skeleton className="h-4 w-28" />
        </div>
        <Skeleton className="h-[38px] w-full" />
        <div className="mt-2 space-y-1">
          {[0, 1, 2, 3, 4].map((item) => (
            <div key={item} className="flex h-9 items-center gap-3 px-2">
              <Skeleton className="h-4 w-4" />
              <Skeleton className={item === 0 ? 'h-3 w-24' : 'h-3 w-20'} />
            </div>
          ))}
        </div>
      </aside>
      <div className="main">
        <header className="topbar portal-loading-topbar">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-7 w-7" variant="pill" />
        </header>
        <main className="content">
          <div className="page-frame dashboard-page portal-loading-page">
            <header className="dashboard-page-header">
              <div className="space-y-3">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-8 w-48" />
                <Skeleton className="h-3 w-80" />
              </div>
              <Skeleton className="h-9 w-24" />
            </header>
            <ListSkeleton rows={5} columns={5} />
          </div>
        </main>
      </div>
    </div>
  );
}
