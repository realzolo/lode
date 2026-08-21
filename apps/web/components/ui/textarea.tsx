import { TextareaHTMLAttributes } from 'react';
import { cx } from '@/lib/cn';

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx('textarea', className)} {...props} />;
}
