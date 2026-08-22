import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

// shadcn/ui Badge. Keeps the app's `variant` union (default | accent | danger |
// warning | success) so pages don't change. `accent` = blue (primary), the rest
// are tinted semantic pills that read well on both dark and light surfaces.
const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-secondary text-secondary-foreground',
        accent: 'border-transparent bg-primary text-primary-foreground',
        success: 'border-transparent bg-emerald-500/15 text-emerald-500',
        warning: 'border-transparent bg-amber-500/15 text-amber-500',
        danger: 'border-transparent bg-red-500/15 text-red-500',
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
