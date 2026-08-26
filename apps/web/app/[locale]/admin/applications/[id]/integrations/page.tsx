'use client';

import { useState } from 'react';
import { ApplicationLoader, IntegrationsSection, makeRefreshDispatcher } from '../sections';

export default function IntegrationsPage({ params }: { params: { id: string } }) {
  const [refreshNonce, setRefreshNonce] = useState(0);
  const onRefresh = makeRefreshDispatcher(setRefreshNonce);
  return <>
    <div className="mb-5"><h1 className="page-title">集成服务</h1><p className="page-subtitle">管理应用独立拥有的只读外部服务连接。</p></div>
    <ApplicationLoader id={params.id} refreshNonce={refreshNonce}>
      {(data) => <IntegrationsSection data={data} appId={params.id} onRefresh={onRefresh} />}
    </ApplicationLoader>
  </>;
}
