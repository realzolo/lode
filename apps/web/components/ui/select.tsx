'use client';

import * as React from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import { cn } from '@/lib/utils';
import { IconCheck, IconChevronDown } from '@/components/icons';

/**
 * Geist-styled Select built on Radix Select.
 *
 * Public API matches the previous native wrapper so existing call sites
 * (members, app-detail sections, admin settings, admin users, workbench
 * explore) keep working without churn: accept `value` / `onChange` /
 * `disabled` + `<option>` children, and synthesise a change event back
 * to the caller with the new value. Internally we render a button-style
 * trigger + Radix-floated content (elevation-5, 6 px radius, Geist body-sm
 * with canvas-soft-2 hover wash and a check indicator on the selected item).
 *
 * The trigger mirrors the compact form-input geometry (h-9, rounded-sm, hairline
 * border) and reuses the canonical Geist focus ring via
 * `focus-visible:shadow-geist-focus`, so it sits in the same row as
 * `<Input>` and `<Textarea>`.
 */
export interface SelectProps {
  value: string;
  onChange: (e: { target: { value: string } }) => void;
  disabled?: boolean;
  className?: string;
  children: React.ReactNode;
  id?: string;
  name?: string;
  'aria-label'?: string;
  'aria-labelledby'?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean;
  'aria-required'?: boolean;
  /** Label to show while the controlled value is empty. If omitted, an empty
   * option supplies it, preserving the existing native-select call sites. */
  placeholder?: string;
}

export function Select({
  value,
  onChange,
  disabled,
  className,
  children,
  id,
  name,
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledBy,
  'aria-describedby': ariaDescribedBy,
  'aria-invalid': ariaInvalid,
  'aria-required': ariaRequired,
  placeholder: placeholderProp,
}: SelectProps) {
  // Build Radix SelectItems from any <option> children. We support plain
  // string / number labels and concatenated string children (everything the
  // current call sites use).
  const optionItems = React.Children.toArray(children)
    .filter(
      (
        c,
      ): c is React.ReactElement<{
        value: string;
        disabled?: boolean;
        children?: React.ReactNode;
      }> => React.isValidElement(c) && (c.type === 'option'),
    )
    .map((opt) => {
      const raw = opt.props.children;
      let label = '';
      if (typeof raw === 'string' || typeof raw === 'number') {
        label = String(raw);
      } else if (Array.isArray(raw)) {
        label = raw
          .filter((x) => typeof x === 'string' || typeof x === 'number')
          .map(String)
          .join('');
      }
      return { value: String(opt.props.value), label, disabled: opt.props.disabled };
    });

  // Radix reserves the empty string for clearing a controlled Select, so it
  // cannot be rendered as an item. Native callers already express an initial
  // hint as `<option value="">...`, which we promote to Value's placeholder.
  const emptyOption = optionItems.find((item) => item.value === '');
  const placeholder = placeholderProp ?? emptyOption?.label;
  const items = optionItems.filter((item) => item.value !== '');

  return (
    <SelectPrimitive.Root
      value={value}
      onValueChange={(v) => onChange({ target: { value: v } })}
      disabled={disabled}
      name={name}
    >
      <SelectPrimitive.Trigger
        id={id}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        aria-invalid={ariaInvalid}
        aria-required={ariaRequired}
        className={cn(
          'flex h-9 w-full items-center justify-between rounded-sm border border-hairline bg-canvas px-3 text-sm text-ink',
          'font-sans',
          'focus:outline-none focus-visible:shadow-geist-focus',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'transition-colors hover:border-hairline-strong',
          'select-trigger',
          '[&>span]:line-clamp-1',
          'data-[placeholder]:text-mute',
          className,
        )}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon asChild>
          <IconChevronDown className="h-4 w-4 shrink-0 text-mute" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={4}
          className={cn(
            'relative z-50 max-h-96 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-sm',
            'border border-hairline bg-canvas text-ink shadow-elevation-5',
            'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-150 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-150',
          )}
        >
          <SelectPrimitive.Viewport className="p-1">
            {items.map((it) => (
              <SelectPrimitive.Item
                key={it.value}
                value={it.value}
                disabled={it.disabled}
                className={cn(
                  'relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-sm outline-none',
                  'focus:bg-canvas-soft-2 data-[highlighted]:bg-canvas-soft-2',
                  'data-[state=checked]:font-medium',
                  'data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
                )}
              >
                <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
                  <SelectPrimitive.ItemIndicator>
                    <IconCheck className="h-3.5 w-3.5 text-ink" />
                  </SelectPrimitive.ItemIndicator>
                </span>
                <SelectPrimitive.ItemText>{it.label}</SelectPrimitive.ItemText>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}
