import type { SVGProps } from 'react';
import { cn } from '@/lib/utils';

interface LodeMarkProps extends SVGProps<SVGSVGElement> {
  signalClassName?: string;
}

export function LodeMark({ className, signalClassName, ...props }: LodeMarkProps) {
  return (
    <svg
      aria-hidden="true"
      className={cn('lode-mark', className)}
      focusable="false"
      viewBox="0 0 32 32"
      {...props}
    >
      <path
        d="M12 3H9C6.5 3 5 5 5 8v4c0 2.5-1 4-3 4 2 0 3 1.5 3 4v4c0 3 1.5 5 4 5h3M20 3h3c2.5 0 4 2 4 5v4c0 2.5 1 4 3 4-2 0-3 1.5-3 4v4c0 3-1.5 5-4 5h-3"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2.6"
      />
      <rect
        className={cn('lode-mark-signal', signalClassName)}
        x="12.5"
        y="12.5"
        width="7"
        height="7"
        rx="1.25"
        fill="currentColor"
      />
    </svg>
  );
}

interface LodeLogoProps {
  className?: string;
  markClassName?: string;
  name?: string;
}

export function LodeLogo({ className, markClassName, name = 'Lode' }: LodeLogoProps) {
  return (
    <span className={cn('lode-logo', className)}>
      <LodeMark className={markClassName} />
      <span>{name}</span>
    </span>
  );
}
