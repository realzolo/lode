import type { ReactNode } from 'react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getMessages } from 'next-intl/server';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import { locales, Locale } from '@/i18n/request';
import { Providers } from '@/components/providers';
import '../globals.css';
import '@xyflow/react/dist/style.css';
import '../investigation.css';
import '../dashboard.css';

export const metadata: Metadata = {
  title: 'Lode',
  description: 'Evidence-driven incident investigation for engineering teams.',
};

// The app is backend-backed, so render dynamically rather than prerendering per locale.
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

  // `NextIntlClientProvider` is rendered inside the client `Providers` component,
  // so it cannot reach next-intl's server-only `getNow()`/`getTimeZone()` helpers.
  // We therefore compute these here (once per request) and forward them explicitly,
  // otherwise client components fall back to an undefined `now`/`timeZone` and the
  // formatter logs a benign-but-noisy `ENVIRONMENT_FALLBACK` error on every render.
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const now = new Date();

  return (
    <html
      lang={locale}
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <body>
        <Providers messages={messages} locale={locale} timeZone={timeZone} now={now}>
          {children}
        </Providers>
      </body>
    </html>
  );
}
