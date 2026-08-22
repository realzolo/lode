'use client';

import { useTranslations } from 'next-intl';
import { ApplicationLoader, PromptsSection } from '../sections';

export default function PromptsPage({ params }: { params: { id: string } }) {
  const t = useTranslations('application');
  return (
    <>
      <h1 className="page-title">{t('prompts')}</h1>
      <ApplicationLoader id={params.id}>
        {(data) => (
          <div style={{ marginTop: 20 }}>
            <PromptsSection data={data} />
          </div>
        )}
      </ApplicationLoader>
    </>
  );
}
