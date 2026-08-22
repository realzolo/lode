import { getRequestConfig } from 'next-intl/server';
import { notFound } from 'next/navigation';

export const locales = ['en', 'zh'] as const;
export const defaultLocale = 'en';
export const localePrefix = 'always' as const;
export type Locale = (typeof locales)[number];

export default getRequestConfig(async ({ requestLocale }) => {
  // next-intl 3.22+ passes the resolved locale via `requestLocale` (a promise).
  // It must be returned from the config, otherwise next-intl falls back to the
  // environment locale during SSR (ENVIRONMENT_FALLBACK) and will hard-error in
  // the next major version.
  const locale = await requestLocale;
  if (!locale || !locales.includes(locale as Locale)) notFound();
  return {
    locale,
    // `timeZone` and `now` must be returned here (not just left to next-intl's
    // defaults) because the client-side `NextIntlClientProvider` inherits them
    // from this config. Without them the formatter logs a benign but noisy
    // `ENVIRONMENT_FALLBACK` error on the client whenever date/time values are
    // formatted (and will hard-error in a future next-intl major version).
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    now: new Date(),
    messages: (await import(`./messages/${locale}.json`)).default,
  };
});
