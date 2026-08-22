'use client';

import { useTranslations } from 'next-intl';
import { ApplicationLoader, ModelSection } from '../sections';

export default function ModelPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  return (
    <>
      <h1 className="page-title">{t('model')}</h1>
      <ApplicationLoader id={params.id}>
        {() => (
          <div style={{ marginTop: 20 }}>
            <ModelSection />
          </div>
        )}
      </ApplicationLoader>
    </>
  );
}
