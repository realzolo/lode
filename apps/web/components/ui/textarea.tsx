import { Textarea as GeistTextareaBase } from '@geist-ui/core';
import type { ComponentType, TextareaHTMLAttributes } from 'react';

// @geist-ui/core's published Textarea type is generated from a `Pick<…ScaleProps…>`
// that breaks native-attribute forwarding. Re-type to the textarea surface plus
// Geist's extras. The runtime component is unaffected.
type GeistTextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  width?: string | number;
  resize?: 'none' | 'both' | 'horizontal' | 'vertical' | 'initial' | 'inherit';
  initialValue?: string;
};
const GeistTextarea = GeistTextareaBase as unknown as ComponentType<GeistTextareaProps>;

// Thin adapter over the official Geist <Textarea>.
export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <GeistTextarea width="100%" className={className} {...props} />;
}
