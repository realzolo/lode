'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, onFocus, readOnly, ...props }, ref) => {
    const [userActivated, setUserActivated] = React.useState(false);
    return (
      <input
        type={type}
        ref={ref}
        className={cn(
          'input flex h-9 w-full rounded-sm border border-input bg-background px-3 py-2 text-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:shadow-geist-focus disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        {...props}
        readOnly={readOnly || !userActivated}
        onFocus={(event) => {
          setUserActivated(true);
          onFocus?.(event);
        }}
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        data-1p-ignore
        data-lpignore="true"
        data-form-type="other"
      />
    );
  },
);
Input.displayName = 'Input';

export { Input };
