'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/lib/navigation';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { clearToken, login, setToken } from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { IconDatabase, IconTerminal, IconBarChart, IconArrowUpRight } from '@/components/icons';

export default function LoginPage() {
  const t = useTranslations('login');
  const tc = useTranslations('common');
  const router = useRouter();
  const { setUser } = useUser();
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
      setUser(result.user);
      // Send the user back to the page they originally requested (set by the
      // middleware as ?redirect), defaulting to the dashboard. Read from the URL
      // directly to avoid pulling in useSearchParams (which would need a Suspense
      // boundary and deopt the page to client rendering).
      const params = new URLSearchParams(window.location.search);
      const redirectRaw = params.get('redirect');
      const redirect =
        redirectRaw && redirectRaw.startsWith('/') && !redirectRaw.startsWith('//')
          ? redirectRaw
          : '/dashboard';
      router.replace(redirect);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-split">
      {/* ---- Brand / hero panel ---- */}
      <aside className="login-aside">
        <div className="login-aside-inner">
          <div className="login-brand">
            <span className="login-mark" aria-hidden="true">▲</span>
            <span className="login-brand-name">{tc('appName')}</span>
          </div>

          <div className="login-hero">
            <h2 className="login-hero-title">{t('asideTitle')}</h2>
            <p className="login-hero-desc">{t('asideDesc')}</p>
          </div>

          <ul className="login-features">
            <li>
              <span className="login-feature-icon"><IconDatabase size={16} /></span>
              <span>{t('feature1')}</span>
            </li>
            <li>
              <span className="login-feature-icon"><IconTerminal size={16} /></span>
              <span>{t('feature2')}</span>
            </li>
            <li>
              <span className="login-feature-icon"><IconBarChart size={16} /></span>
              <span>{t('feature3')}</span>
            </li>
          </ul>
        </div>

        <p className="login-aside-foot">© 2026 {tc('appName')}</p>
      </aside>

      {/* ---- Form panel ---- */}
      <main className="login-main">
        <Card className="login-form-card">
          <div className="login-form-brand">
            <span className="login-mark" aria-hidden="true">▲</span>
            <span className="login-brand-name">{tc('appName')}</span>
          </div>

          <h1 className="login-form-title">{t('title')}</h1>
          <p className="login-form-subtitle">{t('subtitle')}</p>

          <form className="stack" style={{ gap: 16 }} onSubmit={handleSubmit}>
            <div className="field">
              <label className="field-label" htmlFor="email">{t('email')}</label>
              <Input
                id="email"
                type="email"
                placeholder={t('email')}
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
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
              disabled={busy || !email || !password}
            >
              {busy ? <span className="spinner" /> : null}
              {t('submit')}
              {!busy && <IconArrowUpRight size={16} />}
            </Button>
          </form>

          <p className="login-note">{t('newHere')}</p>
        </Card>
      </main>
    </div>
  );
}
