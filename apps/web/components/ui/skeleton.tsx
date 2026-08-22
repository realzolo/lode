'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Geist Skeleton (vercel.com/geist/skeleton).
 *
 * - Mirrors the size/shape of the content it replaces so the layout doesn't
 *   shift when data resolves (avatar → pill, button/chip → rounded, image tile
 *   → squared).
 * - A left→right shimmer sheen animates at 1.5s; pass `noAnimation` to disable
 *   the shimmer on low-power surfaces (prefers-reduced-motion also disables it
 *   via globals.css).
 * - Skeletons are decorative: the region that wraps them should carry
 *   `aria-busy="true"`; the skeleton itself is `aria-hidden`.
 */
type SkeletonVariant = 'rounded' | 'pill' | 'squared';

function Skeleton({
  className,
  variant = 'rounded',
  noAnimation = false,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  variant?: SkeletonVariant;
  noAnimation?: boolean;
}) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'skeleton',
        variant === 'pill' && 'skeleton-pill',
        variant === 'squared' && 'skeleton-squared',
        noAnimation && '[&::after]:hidden',
        className,
      )}
      {...props}
    />
  );
}

export { Skeleton };
