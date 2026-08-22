import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Geist-faithful relative-time label (e.g. "3 days ago", "just now").
// Uses Intl.RelativeTimeFormat so it respects the active locale and stays
// dependency-free. `numeric: 'auto'` yields "yesterday"/"tomorrow" wording.
const RELATIVE_TIME_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 60 * 60 * 24 * 365],
  ['month', 60 * 60 * 24 * 30],
  ['week', 60 * 60 * 24 * 7],
  ['day', 60 * 60 * 24],
  ['hour', 60 * 60],
  ['minute', 60],
];

export function relativeTime(iso: string, locale = 'en'): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.round((then - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  const abs = Math.abs(seconds);
  for (const [unit, secs] of RELATIVE_TIME_UNITS) {
    if (abs >= secs) {
      return rtf.format(Math.round(seconds / secs), unit);
    }
  }
  return rtf.format(Math.round(seconds), 'second');
}
