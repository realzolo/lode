'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { ApplicationLoader, PromptsSection, makeRefreshDispatcher } from '../sections';

export default function PromptsPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  const [refreshNonce, setRefreshNonce] = useState(0);
  const onRefresh = makeRefreshDispatcher(setRefreshNonce);

  return (
    <>
      <h1 className="page-title">{t('prompts')}</h1>
      <ApplicationLoader id={params.id} refreshNonce={refreshNonce}>
        {(data) => (
          <div style={{ marginTop: 20 }}>
            <PromptsSection data={data} appId={params.id} onRefresh={onRefresh} />
          </div>
        )}
      </ApplicationLoader>
    </>
  );
}
