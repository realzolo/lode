import { InputHTMLAttributes } from 'react';
import { cx } from '@/lib/cn';

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx('input', className)} {...props} />;
}
