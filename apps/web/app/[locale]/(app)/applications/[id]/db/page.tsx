'use client';

import { useTranslations } from 'next-intl';
import { ApplicationLoader, DbSourcesSection } from '../sections';

export default function DbSourcesPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  return (
    <>
      <h1 className="page-title">{t('dataSources')}</h1>
      <ApplicationLoader id={params.id}>
        {(data) => (
          <div style={{ marginTop: 20 }}>
            <DbSourcesSection data={data} />
          </div>
        )}
      </ApplicationLoader>
    </>
  );
}
