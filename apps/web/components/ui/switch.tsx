'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Geist-styled Toggle (binary on/off setting).
 *
 * Built on the shadcn "sr-only peer + visual siblings" pattern: a
 * visually-hidden native <input type="checkbox" role="switch"> is the
 * real control (focusable, keyboard-activatable, form-submittable); the
 * track and thumb are aria-hidden spans styled via `peer-checked:` and
 * `peer-focus-visible:` modifiers.
 *
 * No new Radix dependency; works with just the `@radix-ui/react-label`
 * peer-awareness that other primitives already use.
 *
 * Geometry: 36x20 track (w-9 h-5), 16x16 thumb (w-4 h-4) with a 2 px
 * left/start gap, translating 16 px right when checked. Track defaults
 * to a hairline border on canvas-soft-2 and switches to ink-on-ink when
 * checked, matching the Vercel dashboard's toggle chrome. Focus uses
 * the canonical Geist ring (`shadow-geist-focus`).
 */
export interface ToggleProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
  id?: string;
  name?: string;
  /** Accessible label when no visible <label htmlFor> is rendered. */
  'aria-label'?: string;
  'aria-describedby'?: string;
}

export const Toggle = React.forwardRef<HTMLInputElement, ToggleProps>(
  (
    {
      checked,
      onCheckedChange,
      disabled,
      className,
      id,
      name,
      'aria-label': ariaLabel,
      'aria-describedby': ariaDescribedBy,
    },
    ref,
  ) => {
    const generatedId = React.useId();
    const inputId = id ?? generatedId;

    return (
      <label
        className={cn(
          'relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center',
          disabled && 'cursor-not-allowed opacity-50',
          className,
        )}
      >
        <input
          ref={ref}
          id={inputId}
          name={name}
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          aria-label={ariaLabel}
          aria-describedby={ariaDescribedBy}
          aria-checked={checked}
          onChange={(e) => onCheckedChange(e.target.checked)}
          className="peer sr-only"
        />
        {/* Track */}
        <span
          aria-hidden
          className={cn(
            'pointer-events-none absolute inset-0 rounded-full border border-hairline bg-canvas-soft-2 transition-colors',
            'peer-checked:border-ink peer-checked:bg-ink',
            'peer-focus-visible:shadow-geist-focus',
          )}
        />
        {/* Thumb */}
        <span
          aria-hidden
          className={cn(
            'pointer-events-none absolute left-0.5 top-0.5 h-4 w-4 rounded-full border border-hairline-strong bg-canvas shadow-sm',
            'transition-transform peer-checked:translate-x-4 peer-checked:border-ink',
          )}
        />
      </label>
    );
  },
);
Toggle.displayName = 'Toggle';
