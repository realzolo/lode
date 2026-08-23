import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

// shadcn/ui Badge. Keeps the app's `variant` union (default | accent | danger |
// warning | success) so pages don't change. Pills follow DESIGN.md badge-secondary
// (rounded-full). `accent` = brand link blue (primary is now ink/black, so the
// blue highlight lives here); the rest are tinted semantic pills.
const badgeVariants = cva(
  'inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-secondary text-secondary-foreground',
        accent: 'border-transparent bg-[var(--link-bg-soft)] text-[var(--link)]',
        success: 'border-transparent bg-[var(--green-tint)] text-[var(--green)]',
        warning: 'border-transparent bg-[var(--amber-tint)] text-[var(--amber)]',
        danger: 'border-transparent bg-[var(--red-tint)] text-[var(--red)]',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
