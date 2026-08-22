'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { ApplicationLoader, ModelSection, makeRefreshDispatcher } from '../sections';

export default function ModelPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  const [refreshNonce, setRefreshNonce] = useState(0);
  const onRefresh = makeRefreshDispatcher(setRefreshNonce);

  return (
    <>
      <h1 className="page-title">{t('model')}</h1>
      <ApplicationLoader id={params.id} refreshNonce={refreshNonce}>
        {() => (
          <div style={{ marginTop: 20 }}>
            <ModelSection appId={params.id} onRefresh={onRefresh} />
          </div>
        )}
      </ApplicationLoader>
    </>
  );
}
