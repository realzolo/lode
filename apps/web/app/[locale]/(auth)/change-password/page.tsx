'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/lib/navigation';
import { apiErrorMessage, changePassword } from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { Button } from '@/components/ui/button';
import { AuthShell } from '@/components/auth/auth-shell';
import { PasswordField } from '@/components/auth/password-field';
import { Check } from 'lucide-react';

export default function ChangePasswordPage() {
  const router = useRouter();
  const t = useTranslations('passwordChange');
  const tc = useTranslations('common');
  const { user, refresh } = useUser();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hasMinimumLength = newPassword.length >= 8;
  const isDifferent = Boolean(newPassword) && newPassword !== currentPassword;
  const passwordsMatch = Boolean(confirmation) && confirmation === newPassword;
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !currentPassword || !hasMinimumLength || !isDifferent || !passwordsMatch) return;
    setBusy(true);
    setError(null);
    try {
      await changePassword(currentPassword, newPassword);
      await refresh();
      router.replace(user?.is_system_admin ? '/admin' : '/workbench');
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthShell appName={tc('appName')} descriptor={t('footer')}>
      <div className="auth-panel auth-panel-wide" aria-labelledby="password-title">
        <div className="auth-heading">
          <span className="auth-kicker">{t('secureAccess')}</span>
          <h1 id="password-title" className="login-form-title">{t('title')}</h1>
          <p className="login-form-subtitle">{t('subtitle')}</p>
        </div>

        <form autoComplete="on" className="auth-form" onSubmit={submit}>
              <PasswordField
                id="current-password"
                name="current-password"
                autoComplete="current-password"
                label={t('current')}
                placeholder={t('current')}
                showLabel={t('showPassword')}
                hideLabel={t('hidePassword')}
                value={currentPassword}
                disabled={busy}
                onChange={(event) => setCurrentPassword(event.target.value)}
              />
              <div className="auth-form-separator" />
              <PasswordField
                id="new-password"
                name="new-password"
                autoComplete="new-password"
                label={t('new')}
                placeholder={t('new')}
                showLabel={t('showPassword')}
                hideLabel={t('hidePassword')}
                value={newPassword}
                disabled={busy}
                onChange={(event) => setNewPassword(event.target.value)}
              />
              <PasswordField
                id="confirm-password"
                name="confirm-password"
                autoComplete="new-password"
                label={t('confirm')}
                placeholder={t('confirm')}
                showLabel={t('showPassword')}
                hideLabel={t('hidePassword')}
                value={confirmation}
                disabled={busy}
                onChange={(event) => setConfirmation(event.target.value)}
              />

              <ul className="password-requirements" aria-label={t('requirements')}>
                <li data-met={hasMinimumLength || undefined}><Check aria-hidden="true" size={14} />{t('minimumLength')}</li>
                <li data-met={isDifferent || undefined}><Check aria-hidden="true" size={14} />{t('differentFromCurrent')}</li>
                <li data-met={passwordsMatch || undefined}><Check aria-hidden="true" size={14} />{t('passwordsMatch')}</li>
              </ul>

              {error ? <p className="auth-error" role="alert">{error}</p> : null}
              <Button
                className="auth-submit"
                variant="primary"
                type="submit"
                disabled={busy || !currentPassword || !hasMinimumLength || !isDifferent || !passwordsMatch}
                loading={busy}
                loadingText={t('submit')}
              >
                {t('submit')}
              </Button>
        </form>
      </div>
    </AuthShell>
  );
}
