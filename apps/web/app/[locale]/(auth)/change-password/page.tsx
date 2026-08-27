'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/lib/navigation';
import { apiErrorMessage, changePassword } from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

export default function ChangePasswordPage() {
  const router = useRouter();
  const t = useTranslations('passwordChange');
  const tc = useTranslations('common');
  const { user, refresh } = useUser();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
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
  return <main className="auth-shell"><Card className="auth-card"><h1 className="login-form-title">{t('title')}</h1><form className="stack" style={{ gap: 16 }} onSubmit={submit}>
    <Input type="password" autoComplete="current-password" placeholder={t('current')} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
    <Input type="password" autoComplete="new-password" placeholder={t('new')} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
    {error ? <p className="auth-error" role="alert">{error}</p> : null}
    <Button variant="primary" type="submit" disabled={busy || !currentPassword || newPassword.length < 8} loading={busy} loadingText={t('submit')}>{t('submit')}</Button>
  </form></Card></main>;
}
