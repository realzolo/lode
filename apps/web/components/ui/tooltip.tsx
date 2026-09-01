'use client';

import type { ReactElement, ReactNode } from 'react';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { cn } from '@/lib/utils';

interface TooltipProps {
  children: ReactElement;
  content: ReactNode;
  contentClassName?: string;
  side?: 'top' | 'right' | 'bottom' | 'left';
  sideOffset?: number;
}

export function Tooltip({ children, content, contentClassName, side = 'top', sideOffset = 8 }: TooltipProps) {
  return <TooltipPrimitive.Root>
    <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        side={side}
        sideOffset={sideOffset}
        className={cn(
          'z-[80] max-w-60 rounded-[6px] bg-[var(--color-10)] px-2 py-1 text-xs leading-4 text-[var(--color-1)] shadow-elevation-2',
          'animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95',
          contentClassName,
        )}
      >
        {content}
        <TooltipPrimitive.Arrow className="fill-[var(--color-10)]" width={8} height={4} />
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  </TooltipPrimitive.Root>;
}
