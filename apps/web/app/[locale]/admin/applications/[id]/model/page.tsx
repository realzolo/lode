'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import {
  ApplicationLoader,
  ArchitectureContextSection,
  ModelSection,
  makeRefreshDispatcher,
} from '../sections';

export default function ModelPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  const [refreshNonce, setRefreshNonce] = useState(0);
  const onRefresh = makeRefreshDispatcher(setRefreshNonce);

  return (
    <>
      <h1 className="page-title">{t('modelWorkspace')}</h1>
      <ApplicationLoader id={params.id} refreshNonce={refreshNonce}>
        {(data) => (
          <div className="mt-5 space-y-8">
            <section className="space-y-3">
              <div>
                <h2 className="text-sm font-semibold">{t('model')}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{t('modelSelection')}</p>
              </div>
              <ModelSection data={data} appId={params.id} onRefresh={onRefresh} />
            </section>
            <section className="space-y-3">
              <div>
                <h2 className="text-sm font-semibold">{t('architectureContext')}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{t('architectureContextDescription')}</p>
              </div>
              <ArchitectureContextSection data={data} appId={params.id} onRefresh={onRefresh} />
            </section>
          </div>
        )}
      </ApplicationLoader>
    </>
  );
}
