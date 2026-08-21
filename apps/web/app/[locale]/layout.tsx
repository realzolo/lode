import type { ReactNode } from 'react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getMessages } from 'next-intl/server';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import { locales, Locale } from '@/i18n/request';
import { Providers } from '@/components/providers';
import '../globals.css';

export const metadata: Metadata = {
  title: 'Incident Trace',
  description: 'AI-powered root-cause analysis for production errors.',
};

// The app is backend-backed (analyses, applications, memories are fetched at
// request time), so render dynamically rather than prerendering per locale.
export const dynamic = 'force-dynamic';

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: { locale: string };
}) {
  const { locale } = params;
  if (!locales.includes(locale as Locale)) notFound();

  const messages = await getMessages();

  return (
    <html
      lang={locale}
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <body>
        <Providers messages={messages} locale={locale}>
          {children}
        </Providers>
      </body>
    </html>
  );
}
