import { Card as GeistCardBase } from '@geist-ui/core';
import type { ComponentType, HTMLAttributes } from 'react';

// @geist-ui/core's published Card type is generated from a `Pick<…ScaleProps…>`
// that breaks native-attribute forwarding. Re-type to the div surface plus
// Geist's extras. The runtime component is unaffected.
type GeistCardProps = HTMLAttributes<HTMLDivElement> & {
  hoverable?: boolean;
  shadow?: boolean;
  type?: 'default' | 'secondary' | 'success' | 'error' | 'warning' | 'dark';
};
const GeistCard = GeistCardBase as unknown as ComponentType<GeistCardProps>;

// Thin adapter over the official Geist <Card>.
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <GeistCard className={className} {...props} />;
}
