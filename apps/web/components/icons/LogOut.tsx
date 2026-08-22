import * as React from 'react';

// Geist Design System icon (vendored from the official Geist open-source icon set).
// Geist defaults: 24x24, stroke=currentColor, strokeWidth=1.5, round caps/joins.
type IconProps = React.SVGProps<SVGSVGElement> & { size?: number | string };

export function IconLogOut({ size = 24, className, ...props }: IconProps) {
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
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line>
    </svg>
  );
}
