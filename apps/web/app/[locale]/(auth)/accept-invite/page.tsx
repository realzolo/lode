'use client';

import { Suspense, useState } from 'react';
import { useTranslations } from 'next-intl';
import { useSearchParams } from 'next/navigation';
import { useRouter } from '@/lib/navigation';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { acceptInvite } from '@/lib/api';

function AcceptInviteForm() {
  const t = useTranslations('login');
  const tu = useTranslations('users');
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get('token') ?? '';

  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await acceptInvite(token, password, name);
      setDone(true);
      setTimeout(() => router.replace('/login'), 800);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <Card className="auth-card">
        <div className="login-form-brand">
          <span className="login-mark" aria-hidden="true">▲</span>
          <span className="login-brand-name">Lode</span>
        </div>
        <h1 className="login-form-title">{t('acceptInviteTitle')}</h1>
        <p className="login-form-subtitle">{t('acceptInviteSubtitle')}</p>
        {!token && (
          <p className="muted" style={{ color: 'var(--danger)' }}>
            missing or invalid invite token
          </p>
        )}
        {done ? (
          <p className="muted">{t('acceptInviteComplete')}</p>
        ) : (
          <form className="stack" onSubmit={handleSubmit}>
            <Input
              placeholder={tu('name')}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Input
              type="password"
              placeholder={tu('newPassword')}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error && (
              <p className="muted" style={{ color: 'var(--danger)', fontSize: 13 }}>
                {error}
              </p>
            )}
            <Button
              variant="primary"
              type="submit"
              disabled={busy || !token || password.length < 8}
            >
              {t('submit')}
            </Button>
          </form>
        )}
      </Card>
    </div>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={null}>
      <AcceptInviteForm />
    </Suspense>
  );
}
