import { redirect } from 'next/navigation';
import { locales, Locale } from '@/i18n/request';

export default function LocaleIndex({ params }: { params: { locale: string } }) {
  if (!locales.includes(params.locale as Locale)) redirect('/en/dashboard');
  redirect(`/${params.locale}/dashboard`);
}
