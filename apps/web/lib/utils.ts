import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const RELATIVE_TIME_UNITS: [Intl.RelativeTimeFormatUnit, number, string][] = [
  ['day', 60 * 60 * 24, 'd'],
  ['hour', 60 * 60, 'h'],
  ['minute', 60, 'm'],
  ['second', 1, 's'],
];

// Tables use compact relative time for the current week, then a stable date.
// Detail surfaces intentionally keep their complete local timestamps.
export function relativeTime(iso: string, locale = 'en', now = Date.now()): string {
  const timestamp = new Date(iso).getTime();
  if (Number.isNaN(timestamp)) return '\u2014';

  const elapsedSeconds = Math.round((now - timestamp) / 1000);
  const absoluteSeconds = Math.abs(elapsedSeconds);
  if (absoluteSeconds >= 7 * 24 * 60 * 60) {
    return new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric', year: 'numeric' }).format(timestamp);
  }

  const [unit, seconds, abbreviation] = RELATIVE_TIME_UNITS.find(([, value]) => absoluteSeconds >= value) ?? RELATIVE_TIME_UNITS.at(-1)!;
  const amount = Math.max(1, Math.round(absoluteSeconds / seconds));
  if (locale.startsWith('en')) return elapsedSeconds >= 0 ? `${amount}${abbreviation} ago` : `in ${amount}${abbreviation}`;

  return new Intl.RelativeTimeFormat(locale, { numeric: 'always', style: 'short' })
    .format(elapsedSeconds >= 0 ? -amount : amount, unit);
}
