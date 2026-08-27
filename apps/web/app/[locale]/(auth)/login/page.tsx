'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/lib/navigation';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { clearToken, login, setToken } from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { IconArrowUpRight } from '@/components/icons';

export default function LoginPage() {
  const t = useTranslations('login');
  const tc = useTranslations('common');
  const router = useRouter();
  const { setUser } = useUser();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await login(username, password);
      clearToken();
      setToken(result.token);
      setUser(result.user);
      router.replace(
        result.user.must_change_password
          ? '/change-password'
          : result.user.is_system_admin ? '/admin' : '/workbench',
      );
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <Card className="auth-card">
          <div className="login-form-brand">
            <span className="login-mark" aria-hidden="true">▲</span>
            <span className="login-brand-name">{tc('appName')}</span>
          </div>

          <h1 className="login-form-title">{t('title')}</h1>
          <p className="login-form-subtitle">{t('subtitle')}</p>

          <form className="stack" style={{ gap: 16 }} onSubmit={handleSubmit}>
            <div className="field">
              <label className="field-label" htmlFor="username">{t('username')}</label>
              <Input
                id="username"
                placeholder={t('username')}
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="password">{t('password')}</label>
              <Input
                id="password"
                type="password"
                placeholder={t('password')}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            {error && (
              <p className="auth-error" role="alert">{error}</p>
            )}

            <Button
              className="w-full"
              variant="primary"
              type="submit"
              disabled={busy || !username || !password}
            >
              {busy ? <span className="spinner" /> : null}
              {t('submit')}
              {!busy && <IconArrowUpRight size={16} />}
            </Button>
          </form>

      </Card>
    </main>
  );
}
