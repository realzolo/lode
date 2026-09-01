'use client';

import { ReactNode } from 'react';
import { ThemeProvider, useTheme } from 'next-themes';
import { NextIntlClientProvider } from 'next-intl';
import type { AbstractIntlMessages } from 'use-intl';
import { Toaster } from 'sonner';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { UserProvider } from '@/lib/user-context';

interface ProvidersProps {
  children: ReactNode;
  // `getMessages()` returns next-intl's `AbstractIntlMessages`; the provider
  // accepts the same shape. Typed concretely instead of `any` so missing
  // messages fail at compile time rather than silently rendering blanks.
  messages: AbstractIntlMessages;
  locale: string;
  // Forwarded from the server layout because the client provider cannot reach
  // next-intl's server-only `getNow()`/`getTimeZone()` helpers. Required to
  // silence the formatter's `ENVIRONMENT_FALLBACK` error.
  timeZone: string;
  now: Date;
}

// Theme (light/system/dark) is owned by shadcn/ui's CSS variables + next-themes, which
// toggles the `dark`/`light` class on <html>. shadcn/ui components read those
// variables directly, so no provider wrapper is needed (unlike @geist-ui/core).
export function Providers({ children, messages, locale, timeZone, now }: ProvidersProps) {
  return (
    <NextIntlClientProvider messages={messages} locale={locale} timeZone={timeZone} now={now}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
        <TooltipPrimitive.Provider delayDuration={150} skipDelayDuration={0}>
          <UserProvider>{children}</UserProvider>
          <ThemedToaster />
        </TooltipPrimitive.Provider>
      </ThemeProvider>
    </NextIntlClientProvider>
  );
}

function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return <Toaster theme={resolvedTheme === 'dark' ? 'dark' : 'light'} position="bottom-right" />;
}
