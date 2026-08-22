import { Badge as GeistBadgeBase } from '@geist-ui/core';
import type { ComponentType, HTMLAttributes } from 'react';
import { cx } from '@/lib/cn';

type Variant = 'default' | 'accent' | 'danger' | 'warning' | 'success';

// Geist's Badge only has neutral/success/error/warning types (no blue "accent"),
// so we fold our legacy `accent` variant into the neutral default.
const TYPE: Record<Variant, 'default' | 'success' | 'error' | 'warning'> = {
  default: 'default',
  accent: 'default',
  success: 'success',
  warning: 'warning',
  danger: 'error',
};

// @geist-ui/core's published Badge type is generated from a `Pick<…ScaleProps…>`
// that breaks native-attribute forwarding. Re-type to the span surface plus
// Geist's extras. The runtime component is unaffected.
type GeistBadgeProps = HTMLAttributes<HTMLSpanElement> & {
  type?: 'default' | 'success' | 'error' | 'warning';
  dot?: boolean;
  anchor?: boolean;
};
const GeistBadge = GeistBadgeBase as unknown as ComponentType<GeistBadgeProps>;

// Thin adapter over the official Geist <Badge>.
export function Badge({
  variant = 'default',
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  return <GeistBadge type={TYPE[variant]} className={cx(className)} {...props} />;
}
