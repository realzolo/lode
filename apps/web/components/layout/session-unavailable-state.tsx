'use client';

import { useState } from 'react';
import { CloudOff, RefreshCw } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { EmptyState } from '@/components/ui/empty-state';
import { Button } from '@/components/ui/button';
import { useUser } from '@/lib/user-context';

// A valid cookie can outlive a temporary backend outage. Keep that recoverable
// state distinct from an expired session so middleware cannot bounce users
// between the protected route and /login without rendering anything useful.
export function SessionUnavailableState() {
  const t = useTranslations('common');
  const { refresh } = useUser();
  const [retrying, setRetrying] = useState(false);

  async function retry() {
    setRetrying(true);
    await refresh();
    setRetrying(false);
  }

  return (
    <main className="session-unavailable">
      <EmptyState
        icon={<CloudOff size={20} />}
        title={t('sessionUnavailable')}
        description={t('sessionUnavailableDescription')}
        action={(
          <Button variant="primary" loading={retrying} onClick={() => void retry()}>
            <RefreshCw size={15} />
            {t('retry')}
          </Button>
        )}
      />
    </main>
  );
}
