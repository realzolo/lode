import { HTMLAttributes } from 'react';
import { cx } from '@/lib/cn';

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('card', className)} {...props} />;
}
