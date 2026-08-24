'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { ApplicationLoader, DescriptionsSection, makeRefreshDispatcher } from '../sections';

export default function DescriptionsPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  const [refreshNonce, setRefreshNonce] = useState(0);
  const onRefresh = makeRefreshDispatcher(setRefreshNonce);

  return (
    <>
      <h1 className="page-title">{t('descriptions')}</h1>
      <ApplicationLoader id={params.id} refreshNonce={refreshNonce}>
        {(data) => (
          <div style={{ marginTop: 20 }}>
            <DescriptionsSection data={data} appId={params.id} onRefresh={onRefresh} />
          </div>
        )}
      </ApplicationLoader>
    </>
  );
}
