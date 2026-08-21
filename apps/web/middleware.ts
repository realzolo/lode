import createMiddleware from 'next-intl/middleware';
import { locales, defaultLocale } from './i18n/request';

export default createMiddleware({
  locales,
  defaultLocale,
  localePrefix: 'always',
});

export const config = {
  // Skip API, static assets, and files with an extension.
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)'],
};
