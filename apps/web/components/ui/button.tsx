import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { LoaderCircle } from 'lucide-react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { Tooltip } from '@/components/ui/tooltip';

// Vercel Dashboard-style button. The app's pages pass
// `variant="primary" | "default"` and `size="sm" | "default"`, while native
// attrs keep flowing through for forms and icon-only actions.
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[6px] text-sm font-medium transition-[border-color,background,color,box-shadow] duration-150 ease-out-strong focus-visible:outline-none focus-visible:shadow-geist-focus disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-wait aria-disabled:opacity-50 data-[loading]:cursor-wait [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default:
          'border border-transparent bg-[var(--color-10)] text-[var(--color-1)] hover:bg-[var(--color-8)] active:bg-[var(--color-7)]',
        primary: 'border border-transparent bg-[var(--color-10)] text-[var(--color-1)] hover:bg-[var(--color-8)] active:bg-[var(--color-7)]',
        secondary: 'border border-transparent bg-[var(--dashboard-hover)] text-[var(--color-10)] hover:bg-[var(--color-3)] active:bg-[var(--color-4)]',
        outline:
          'border border-[var(--dashboard-border)] bg-[var(--dashboard-panel)] text-[var(--color-10)] hover:border-[var(--dashboard-strong-border)] hover:bg-[var(--dashboard-hover)] active:bg-[var(--color-3)]',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90 active:bg-destructive/80',
        ghost: 'text-[var(--color-10)] hover:bg-[var(--dashboard-hover)] active:bg-[var(--color-3)]',
        link: 'text-[var(--link)] underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-9 px-2.5 py-0',
        sm: 'h-8 px-1.5 py-0 text-sm',
        lg: 'h-10 rounded-[8px] px-3.5 text-base',
        icon: 'h-8 w-8',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
  loadingText?: string;
}

function buttonText(node: React.ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(buttonText).join('');
  if (React.isValidElement<{ children?: React.ReactNode }>(node)) return buttonText(node.props.children);
  return '';
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, loading = false, loadingText, children, disabled, onClick, type = 'button', title: nativeTitle, ...props }, ref) => {
    const loadingContent = loadingText ?? (size === 'icon' ? null : buttonText(children));
    const childTooltip = asChild && React.isValidElement<{ title?: string; 'aria-label'?: string }>(children)
      ? children.props.title ?? children.props['aria-label']
      : undefined;
    const tooltipText = size === 'icon' ? nativeTitle ?? props['aria-label'] ?? childTooltip : undefined;
    const triggerChildren = asChild && tooltipText && React.isValidElement<{ title?: string }>(children)
      ? React.cloneElement(children, { title: undefined })
      : children;
    const handleClick: React.MouseEventHandler<HTMLButtonElement> = (event) => {
      if (loading) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      onClick?.(event);
    };
    const control = asChild ? (
        <Slot
          className={cn(buttonVariants({ variant, size, className }))}
          ref={ref}
          aria-busy={loading || undefined}
          aria-disabled={loading || undefined}
          data-loading={loading || undefined}
          onClick={handleClick}
          {...props}
        >
          {triggerChildren}
        </Slot>
      ) : (
      <button
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        aria-busy={loading || undefined}
        aria-disabled={loading || undefined}
        data-loading={loading || undefined}
        disabled={disabled || loading}
        onClick={handleClick}
        type={type}
        title={tooltipText ? undefined : nativeTitle}
        {...props}
      >
        {loading ? <LoaderCircle className="button-spinner animate-spin" aria-hidden="true" /> : null}
        {loading ? loadingContent : children}
      </button>
    );
    return tooltipText ? <Tooltip content={tooltipText}>{control}</Tooltip> : control;
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
