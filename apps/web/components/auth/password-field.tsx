'use client';

import { useState } from 'react';
import type { InputHTMLAttributes } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Input } from '@/components/ui/input';

interface PasswordFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  hideLabel: string;
  label: string;
  showLabel: string;
}

export function PasswordField({ hideLabel, id, label, showLabel, ...props }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  const actionLabel = visible ? hideLabel : showLabel;
  const Icon = visible ? EyeOff : Eye;

  return (
    <label className="auth-field" htmlFor={id}>
      <span className="auth-field-label">{label}</span>
      <span className="auth-input-control">
        <Input id={id} type={visible ? 'text' : 'password'} className="auth-input-with-action" {...props} />
        <button
          aria-label={actionLabel}
          className="auth-input-action"
          onClick={() => setVisible((value) => !value)}
          title={actionLabel}
          type="button"
        >
          <Icon aria-hidden="true" size={16} />
        </button>
      </span>
    </label>
  );
}
