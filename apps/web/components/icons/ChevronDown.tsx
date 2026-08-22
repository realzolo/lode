import * as React from 'react';

// Geist Design System icon (vendored from the official Geist open-source icon set).
// Geist defaults: 24x24, stroke=currentColor, strokeWidth=1.5, round caps/joins.
type IconProps = React.SVGProps<SVGSVGElement> & { size?: number | string };

export function IconChevronDown({ size = 24, className, ...props }: IconProps) {
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
      <polyline points="6 9 12 15 18 9"></polyline>
    </svg>
  );
}
