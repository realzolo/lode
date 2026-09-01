'use client';

import * as React from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export type CheckboxProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'>;

// Keeps the native checkbox as the focus and form target while rendering the
// compact four-pixel-radius control used by the dashboard.
const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, disabled, ...props }, ref) => (
    <span className={cn('relative inline-flex size-4 shrink-0 align-middle', className)}>
      <input
        ref={ref}
        type="checkbox"
        disabled={disabled}
        className="peer absolute inset-0 z-10 m-0 size-4 cursor-pointer appearance-none opacity-0 disabled:cursor-not-allowed"
        {...props}
      />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-[4px] border border-[var(--dashboard-strong-border)] bg-[var(--dashboard-panel)] text-[var(--color-1)] transition-[background,border-color,box-shadow] duration-150 peer-checked:border-[var(--color-10)] peer-checked:bg-[var(--color-10)] peer-checked:[&>svg]:opacity-100 peer-focus-visible:shadow-geist-focus peer-disabled:border-[var(--dashboard-border)] peer-disabled:bg-[var(--dashboard-hover)]"
      >
        <Check className="size-3 opacity-0 transition-opacity duration-150" strokeWidth={2.5} />
      </span>
    </span>
  ),
);
Checkbox.displayName = 'Checkbox';

export { Checkbox };
