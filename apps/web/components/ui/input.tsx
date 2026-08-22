import { Input as GeistInputBase } from '@geist-ui/core';
import type { ComponentType, InputHTMLAttributes, ReactNode } from 'react';

// @geist-ui/core's published Input type is generated from a `Pick<…ScaleProps…>`
// that breaks native-attribute forwarding. Re-type to the input surface plus
// Geist's extras. The runtime component is unaffected.
type GeistInputProps = InputHTMLAttributes<HTMLInputElement> & {
  type?: 'default' | 'secondary' | 'success' | 'error' | 'warning';
  htmlType?: string;
  width?: string | number;
  icon?: ReactNode;
  iconRight?: ReactNode;
};
const GeistInput = GeistInputBase as unknown as ComponentType<GeistInputProps>;

// Thin adapter over the official Geist <Input>. The native HTML type (text/
// password/...) is forwarded as `htmlType` because Geist reserves `type` for its
// own visual variants (default/secondary/success/error/warning). Width defaults
// to 100% so inputs fill form rows like the previous wrapper did.
export function Input({ className, type, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <GeistInput width="100%" htmlType={type} className={className} {...props} />;
}
