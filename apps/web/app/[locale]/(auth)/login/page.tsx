'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/lib/navigation';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { clearToken, login, setRole, setToken } from '@/lib/api';

export default function LoginPage() {
  const t = useTranslations('login');
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await login(email, password);
      clearToken();
      setToken(result.token);
      setRole(result.user.role);
      router.replace('/dashboard');
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <Card className="auth-card">
        <h1 className="page-title">{t('title')}</h1>
        <p className="page-subtitle">{t('subtitle')}</p>
        <form className="stack" onSubmit={handleSubmit}>
          <Input
            type="email"
            placeholder={t('email')}
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Input
            type="password"
            placeholder={t('password')}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && (
            <p className="muted" style={{ color: 'var(--danger, #f87171)', fontSize: 13 }}>
              {error}
            </p>
          )}
          <Button variant="primary" type="submit" disabled={busy || !email || !password}>
            {t('submit')}
          </Button>
        </form>
      </Card>
    </div>
  );
}
