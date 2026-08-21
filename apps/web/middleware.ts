import createMiddleware from 'next-intl/middleware';
import { locales, defaultLocale, localePrefix } from './i18n/request';

export default createMiddleware({
  locales,
  defaultLocale,
  localePrefix,
});

export const config = {
  // Skip API, static assets, and files with an extension.
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)'],
};
