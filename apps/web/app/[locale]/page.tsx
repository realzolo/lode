import { redirect } from 'next/navigation';
import { locales, Locale } from '@/i18n/request';

export default function LocaleIndex({ params }: { params: { locale: string } }) {
  // Land on the admin console; the admin layout's role gate will bounce
  // non-admins to /workbench. The JWT has no role claim, so we can't decide
  // the portal server-side here — the client gate handles it.
  if (!locales.includes(params.locale as Locale)) redirect('/en/admin');
  redirect(`/${params.locale}/admin`);
}
