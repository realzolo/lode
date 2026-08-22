import * as React from 'react';

// Geist Design System icon (vendored from the official Geist open-source icon set).
// Geist defaults: 24x24, stroke=currentColor, strokeWidth=1.5, round caps/joins.
type IconProps = React.SVGProps<SVGSVGElement> & { size?: number | string };

export function IconArrowUpRight({ size = 24, className, ...props }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      shapeRendering="geometricPrecision"
      className={className}
      aria-hidden="true"
      {...props}
    >
      <line x1="7" y1="17" x2="17" y2="7"></line><polyline points="7 7 17 7 17 17"></polyline>
    </svg>
  );
}
