'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import type { AnalysisStatus } from '@/lib/types';
import {
  addHint,
  fetchAnalysis,
  reanalyze,
  toUiSteps,
  type AnalysisDetail,
} from '@/lib/api';

function statusVariant(status: AnalysisStatus): 'success' | 'warning' | 'danger' | 'accent' | 'default' {
  switch (status) {
    case 'completed':
      return 'success';
    case 'failed':
      return 'danger';
    case 'running':
    case 'needs_human':
      return 'warning';
    case 'pending':
    default:
      return 'accent';
  }
}

export default function AnalysisPage({ params }: { params: { dedupeKey: string } }) {
  const t = useTranslations('analysis');
  const tc = useTranslations('common');
  const dedupeKey = decodeURIComponent(params.dedupeKey);

  const [detail, setDetail] = useState<AnalysisDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState('');
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchAnalysis(dedupeKey);
      setDetail(data);
      setError(null);
      if (data.status === 'running') {
        // Poll until the engine finishes.
        pollRef.current = setTimeout(() => void load(), 1500);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [dedupeKey]);

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [load]);

  async function handleReanalyze() {
    setBusy(true);
    try {
      await reanalyze(dedupeKey);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function handleAddHint() {
    if (!hint.trim()) return;
    setBusy(true);
    try {
      await addHint(dedupeKey, hint.trim());
      setHint('');
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (loading && !detail) return <p className="muted">{tc('loading')}</p>;
  if (error) return <p className="muted" style={{ color: 'var(--danger, #f87171)' }}>{error}</p>;
  if (!detail) return <p className="muted">{tc('empty')}</p>;

  const uiStatus = detail.status as AnalysisStatus;
  const steps = toUiSteps(detail.steps);
  const evidenceText = detail.evidence
    ? JSON.stringify(detail.evidence, null, 2)
    : '';

  return (
    <>
      <div className="row-between">
        <div>
          <h1 className="page-title">{t('title')}</h1>
          <p className="mono muted" style={{ fontSize: 13 }}>
            {dedupeKey}
          </p>
        </div>
        <div className="row">
          <Badge variant={statusVariant(uiStatus)}>{uiStatus}</Badge>
          <Button variant="primary" onClick={handleReanalyze} disabled={busy}>
            {tc('reanalyze')}
          </Button>
        </div>
      </div>

      <div className="stack" style={{ marginTop: 20 }}>
        <Card>
          <div className="row-between">
            <h2 className="section-title">{t('conclusion')}</h2>
            <span className="muted">
              {t('confidence')}: {detail.confidence != null ? detail.confidence.toFixed(2) : '—'}
            </span>
          </div>
          <p style={{ marginTop: 8 }}>{detail.conclusion ?? tc('loading')}</p>
          {detail.matched_memory && (
            <p className="muted" style={{ marginTop: 8, fontSize: 13 }}>
              ⊹ matched shared memory: {detail.matched_memory}
            </p>
          )}
        </Card>

        <Card>
          <h2 className="section-title">{t('evidence')}</h2>
          <pre className="evidence">{evidenceText}</pre>
        </Card>

        <Card>
          <h2 className="section-title">{t('steps')}</h2>
          <div className="flow">
            {steps.map((step, i) => (
              <div key={step.nodeType}>
                <div className="flow-node">
                  <span className={['flow-dot', step.status].join(' ')} />
                  <div>
                    <div className="flow-title">{step.title}</div>
                    {step.detail && <div className="muted flow-detail">{step.detail}</div>}
                  </div>
                </div>
                {i < steps.length - 1 && <div className="flow-line" />}
              </div>
            ))}
          </div>
        </Card>

        {detail.hints.length > 0 && (
          <Card className="stack">
            <h2 className="section-title">{tc('addHint')}</h2>
            {detail.hints.map((h) => (
              <div key={h.id} className="muted" style={{ fontSize: 13 }}>
                <span className="mono">{h.author}</span>: {h.content}
              </div>
            ))}
          </Card>
        )}

        <Card className="stack">
          <Textarea
            placeholder={t('hintPlaceholder')}
            value={hint}
            onChange={(e) => setHint(e.target.value)}
          />
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <Button variant="primary" onClick={handleAddHint} disabled={busy || !hint.trim()}>
              {tc('addHint')}
            </Button>
          </div>
        </Card>
      </div>
    </>
  );
}
