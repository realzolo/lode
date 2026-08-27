import createMiddleware from 'next-intl/middleware';
import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';
import { locales, defaultLocale, localePrefix } from './i18n/request';

const intlMiddleware = createMiddleware({
  locales,
  defaultLocale,
  localePrefix,
});

// Routes that require an authenticated session. The app is split into two
// portals under real top-level segments: `/admin` (Admin Console) and
// `/workbench` (Developer Workbench). Everything under either is gated, so we
// Password rotation is likewise session-only. Deeper paths inherit the parent.
const PROTECTED_SEGMENTS = new Set(['admin', 'workbench', 'change-password']);

const TOKEN_COOKIE = 'lode_token';

// Split a locale-prefixed pathname into its locale and the remaining path.
function stripLocale(pathname: string): { locale: string; rest: string } {
  const segments = pathname.split('/').filter(Boolean);
  const head = segments[0] ?? '';
  const hasLocale = (locales as readonly string[]).includes(head);
  const rest = '/' + (hasLocale ? segments.slice(1) : segments).join('/');
  return { locale: hasLocale ? head : defaultLocale, rest };
}

// Middleware only checks cookie *presence* and *expiry* (decoded, not signature-
// verified) — the backend remains the authority that verifies the HMAC signature
// on every API call. Verifying the signature here would mean shipping the signing
// secret to the browser, which we must not do.
function isSessionValid(token: string | undefined): boolean {
  if (!token) return false;
  const exp = jwtExp(token);
  if (typeof exp !== 'number') return false; // unreadable → fail-safe
  return exp > Math.floor(Date.now() / 1000);
}

function jwtExp(token: string): number | undefined {
  try {
    const payload = JSON.parse(base64UrlDecode(token.split('.')[1] ?? ''));
    return typeof payload?.exp === 'number' ? payload.exp : undefined;
  } catch {
    return undefined;
  }
}

function base64UrlDecode(input: string): string {
  const b64 = input.replace(/-/g, '+').replace(/_/g, '/');
  return atob(b64);
}

export default function middleware(request: NextRequest): NextResponse {
  const { locale, rest } = stripLocale(request.nextUrl.pathname);
  const segment = rest.split('/').filter(Boolean)[0] ?? '';

  const token = request.cookies.get(TOKEN_COOKIE)?.value;
  const authed = isSessionValid(token);

  if (PROTECTED_SEGMENTS.has(segment) && !authed) {
    const url = new URL(`/${locale}/login`, request.url);
    // Preserve the originally-requested path so login can bounce back. Fall
    // back to the admin console (the client role gate re-routes non-admins).
    url.searchParams.set('redirect', rest === '/' ? '/admin' : rest);
    return NextResponse.redirect(url);
  }

  // Already-authenticated users shouldn't land on the login screen. We can't
  // read role from the (role-less) JWT here, so bounce to /admin and let the
  // client-side gate send non-admins to /workbench.
  if (segment === 'login' && authed) {
    return NextResponse.redirect(new URL(`/${locale}/admin`, request.url));
  }

  return intlMiddleware(request);
}

export const config = {
  // Analysis routes carry opaque hexadecimal IDs, never producer-controlled
  // dedupe keys. Keeping the standard static-file exclusion therefore cannot
  // bypass a business route while assets stay outside locale/auth middleware.
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)'],
};
