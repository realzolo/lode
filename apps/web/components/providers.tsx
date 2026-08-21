'use client';

import { ReactNode } from 'react';
import { ThemeProvider } from 'next-themes';
import { NextIntlClientProvider } from 'next-intl';

interface ProvidersProps {
  children: ReactNode;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  messages: any;
  locale: string;
}

export function Providers({ children, messages, locale }: ProvidersProps) {
  return (
    <NextIntlClientProvider messages={messages} locale={locale}>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
        {children}
      </ThemeProvider>
    </NextIntlClientProvider>
  );
}
