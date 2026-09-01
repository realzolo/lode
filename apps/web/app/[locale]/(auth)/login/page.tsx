'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/lib/navigation';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { AuthShell } from '@/components/auth/auth-shell';
import { PasswordField } from '@/components/auth/password-field';
import { apiErrorMessage, clearToken, login, setToken } from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { IconArrowUpRight } from '@/components/icons';

export default function LoginPage() {
  const t = useTranslations('login');
  const tc = useTranslations('common');
  const router = useRouter();
  const { loading, setUser, user } = useUser();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (loading || !user) return;
    router.replace(
      user.must_change_password
        ? '/change-password'
        : user.is_system_admin ? '/admin' : '/workbench',
    );
  }, [loading, router, user]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !username || !password) return;
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
      setError(apiErrorMessage(err, tc('requestFailed')));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell appName={tc('appName')} descriptor={t('footer')}>
      <div className="auth-panel" aria-labelledby="login-title">
        <div className="auth-heading">
          <span className="auth-kicker">{t('secureAccess')}</span>
          <h1 id="login-title" className="login-form-title">{t('title')}</h1>
          <p className="login-form-subtitle">{t('subtitle')}</p>
        </div>

        <form autoComplete="on" className="auth-form" onSubmit={handleSubmit}>
              <label className="auth-field" htmlFor="username">
                <span className="auth-field-label">{t('username')}</span>
                <Input
                  id="username"
                  name="username"
                  autoComplete="username"
                  placeholder={t('username')}
                  value={username}
                  disabled={busy}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <PasswordField
                id="password"
                name="password"
                autoComplete="current-password"
                label={t('password')}
                placeholder={t('password')}
                showLabel={t('showPassword')}
                hideLabel={t('hidePassword')}
                value={password}
                disabled={busy}
                onChange={(e) => setPassword(e.target.value)}
              />

              {error && (
                <p className="auth-error" role="alert">{error}</p>
              )}

              <Button
                className="auth-submit"
                variant="primary"
                type="submit"
                disabled={busy || !username || !password}
                loading={busy}
                loadingText={t('submit')}
              >
                {t('submit')}
                {!busy && <IconArrowUpRight size={16} />}
              </Button>
        </form>
      </div>
    </AuthShell>
  );
}
