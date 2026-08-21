import { HTMLAttributes } from 'react';
import { cx } from '@/lib/cn';

type Variant = 'default' | 'accent' | 'danger' | 'warning' | 'success';

export function Badge({
  variant = 'default',
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  return (
    <span
      className={cx('badge', variant !== 'default' && `badge-${variant}`, className)}
      {...props}
    />
  );
}
