'use client';

import { useTranslations } from 'next-intl';
import { ApplicationLoader, ReposSection } from '../sections';

export default function ReposPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  return (
    <>
      <h1 className="page-title">{t('repositories')}</h1>
      <ApplicationLoader id={params.id}>
        {(data) => (
          <div style={{ marginTop: 20 }}>
            <ReposSection data={data} />
          </div>
        )}
      </ApplicationLoader>
    </>
  );
}
