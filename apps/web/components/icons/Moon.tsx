import * as React from 'react';

// Geist Design System icon (vendored from the official Geist open-source icon set).
// Geist defaults: 24x24, stroke=currentColor, strokeWidth=1.5, round caps/joins.
type IconProps = React.SVGProps<SVGSVGElement> & { size?: number | string };

export function IconMoon({ size = 24, className, ...props }: IconProps) {
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
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
    </svg>
  );
}
