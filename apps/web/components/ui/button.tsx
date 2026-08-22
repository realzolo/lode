import { forwardRef } from 'react';
import { Button as GeistButtonBase } from '@geist-ui/core';
import type {
  ButtonHTMLAttributes,
  ForwardRefExoticComponent,
  ReactNode,
  RefAttributes,
} from 'react';
import { cx } from '@/lib/cn';

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'default' | 'primary';
  size?: 'default' | 'sm';
};

// @geist-ui/core ships a malformed Button type: its generated prop type makes
// input-only attributes (e.g. `placeholder`) required and conflicts `type` with
// the native HTML attribute type. Re-type to the standard button surface plus
// Geist's own extras so native props (onClick, disabled, aria-*, ...) forward
// cleanly. The runtime component is unaffected.
type GeistButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> & {
  type?: string;
  htmlType?: string;
  icon?: ReactNode;
  iconRight?: ReactNode;
  loading?: boolean;
};
const GeistButton = GeistButtonBase as unknown as ForwardRefExoticComponent<
  GeistButtonProps & RefAttributes<HTMLButtonElement>
>;

// Thin adapter over the official Geist <Button>. Geist's primary (filled,
// theme-inverted) button is the component's DEFAULT; `variant="default"` maps
// to Geist's outlined `secondary`. The native HTML button type is forwarded as
// `htmlType` (Geist reserves `type` for its own visual variants).
export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = 'default', size: _size, className, type, ...props },
  ref,
) {
  return (
    <GeistButton
      ref={ref}
      type={variant === 'primary' ? undefined : 'secondary'}
      htmlType={type}
      className={className}
      {...props}
    />
  );
});
