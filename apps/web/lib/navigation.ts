import { createSharedPathnamesNavigation } from 'next-intl/navigation';
import { locales, localePrefix } from '@/i18n/request';

// Locale-aware navigation primitives. `Link` and `usePathname` automatically
// apply / read the active locale prefix, so call sites never hard-code it.
export const { Link, redirect, usePathname, useRouter } = createSharedPathnamesNavigation({
  locales,
  localePrefix,
});
