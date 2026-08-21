import { ButtonHTMLAttributes } from 'react';
import { cx } from '@/lib/cn';

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'default' | 'primary';
  size?: 'default' | 'sm';
};

export function Button({ variant = 'default', size = 'default', className, ...props }: Props) {
  return (
    <button
      className={cx('btn', variant === 'primary' && 'btn-primary', size === 'sm' && 'btn-sm', className)}
      {...props}
    />
  );
}
