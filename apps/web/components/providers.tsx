'use client';

import { ReactNode } from 'react';
import { ThemeProvider } from 'next-themes';
import { NextIntlClientProvider } from 'next-intl';
import type { AbstractIntlMessages } from 'use-intl';
import { UserProvider } from '@/lib/user-context';

interface ProvidersProps {
  children: ReactNode;
  // `getMessages()` returns next-intl's `AbstractIntlMessages`; the provider
  // accepts the same shape. Typed concretely instead of `any` so missing
  // messages fail at compile time rather than silently rendering blanks.
  messages: AbstractIntlMessages;
  locale: string;
}

export function Providers({ children, messages, locale }: ProvidersProps) {
  return (
    <NextIntlClientProvider messages={messages} locale={locale}>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
        <UserProvider>{children}</UserProvider>
      </ThemeProvider>
    </NextIntlClientProvider>
  );
}
