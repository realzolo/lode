import * as React from 'react';
import { cn } from '@/lib/utils';

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, autoComplete, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        'textarea flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:shadow-geist-focus disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
      autoComplete={autoComplete ?? 'off'}
      autoCorrect="off"
      autoCapitalize="off"
      spellCheck={false}
    />
  ),
);
Textarea.displayName = 'Textarea';

export { Textarea };
