'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { useRouter } from '@/lib/navigation';
import { changePassword } from '@/lib/api';
import { useUser } from '@/lib/user-context';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

export default function ChangePasswordPage() {
  const router = useRouter();
  const t = useTranslations('passwordChange');
  const { user, refresh } = useUser();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    try {
      await changePassword(currentPassword, newPassword);
      await refresh();
      router.replace(user?.is_system_admin ? '/admin' : '/workbench');
    } catch (cause) { setError(String(cause)); }
  }
  return <main className="auth-shell"><Card className="auth-card"><h1 className="login-form-title">{t('title')}</h1><form className="stack" style={{ gap: 16 }} onSubmit={submit}>
    <Input type="password" autoComplete="current-password" placeholder={t('current')} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
    <Input type="password" autoComplete="new-password" placeholder={t('new')} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
    {error ? <p className="auth-error" role="alert">{error}</p> : null}
    <Button variant="primary" type="submit" disabled={!currentPassword || newPassword.length < 8}>{t('submit')}</Button>
  </form></Card></main>;
}
