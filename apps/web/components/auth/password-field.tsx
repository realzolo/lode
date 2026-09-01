'use client';

import { useState } from 'react';
import type { InputHTMLAttributes } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Tooltip } from '@/components/ui/tooltip';

interface PasswordFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  hideLabel: string;
  label: string;
  showLabel: string;
}

export function PasswordField({ hideLabel, id, label, showLabel, disabled, ...props }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  const actionLabel = visible ? hideLabel : showLabel;
  const Icon = visible ? EyeOff : Eye;

  return (
    <div className="auth-field">
      <label className="auth-field-label" htmlFor={id}>{label}</label>
      <span className="auth-input-control">
        <Input id={id} type={visible ? 'text' : 'password'} disabled={disabled} className="auth-input-with-action" {...props} />
        <Tooltip content={actionLabel}>
          <button
            aria-label={actionLabel}
            aria-pressed={visible}
            disabled={disabled}
            className="auth-input-action"
            onClick={() => setVisible((value) => !value)}
            type="button"
          >
            <Icon aria-hidden="true" size={16} />
          </button>
        </Tooltip>
      </span>
    </div>
  );
}
