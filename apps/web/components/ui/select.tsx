import { SelectHTMLAttributes } from 'react';
import { cx } from '@/lib/cn';

export function Select({
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cx('input', 'select', className)} {...props} />;
}
