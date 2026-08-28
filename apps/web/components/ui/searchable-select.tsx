'use client';

import * as React from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Check, ChevronsUpDown, LoaderCircle, Search } from 'lucide-react';
import { Command } from 'cmdk';
import { cn } from '@/lib/utils';

export interface SearchableSelectOption {
  value: string;
  label: string;
  description?: string;
  keywords?: string;
  disabled?: boolean;
}

interface SearchableSelectProps {
  value: string;
  onValueChange: (value: string) => void;
  options: SearchableSelectOption[];
  placeholder: string;
  searchPlaceholder: string;
  emptyMessage: string;
  disabled?: boolean;
  loading?: boolean;
  ariaLabel?: string;
}

export function SearchableSelect({
  value,
  onValueChange,
  options,
  placeholder,
  searchPlaceholder,
  emptyMessage,
  disabled = false,
  loading = false,
  ariaLabel,
}: SearchableSelectProps) {
  const [open, setOpen] = React.useState(false);
  const selected = options.find((option) => option.value === value);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          role="combobox"
          aria-label={ariaLabel}
          aria-expanded={open}
          disabled={disabled || loading}
          className="flex min-h-9 w-full min-w-0 items-center justify-between gap-2 rounded-sm border border-input bg-background px-3 py-2 text-left text-sm outline-none transition hover:bg-accent focus-visible:shadow-geist-focus disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span className={cn('min-w-0 truncate', !selected && 'text-muted-foreground')} title={selected?.label}>
            {selected?.label ?? placeholder}
          </span>
          {loading ? (
            <LoaderCircle className="size-4 shrink-0 animate-spin text-muted-foreground" />
          ) : (
            <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" />
          )}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          className="z-[70] w-[var(--radix-popover-trigger-width)] min-w-64 max-w-[calc(100vw-2rem)] overflow-hidden rounded-sm border bg-popover text-popover-foreground shadow-elevation-4"
        >
          <Command className="w-full" filter={(optionValue, search, keywords) => {
            const haystack = `${optionValue} ${(keywords || []).join(' ')}`.toLowerCase();
            return haystack.includes(search.trim().toLowerCase()) ? 1 : 0;
          }}>
            <div className="flex h-10 items-center gap-2 border-b px-3">
              <Search className="size-4 shrink-0 text-muted-foreground" />
              <Command.Input
                autoFocus
                placeholder={searchPlaceholder}
                className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
            </div>
            <Command.List className="max-h-64 overflow-y-auto p-1">
              <Command.Empty className="px-3 py-8 text-center text-sm text-muted-foreground">
                {emptyMessage}
              </Command.Empty>
              {options.map((option) => (
                <Command.Item
                  key={option.value}
                  value={option.value}
                  keywords={[option.label, option.description || '', option.keywords || '']}
                  disabled={option.disabled}
                  onSelect={() => {
                    onValueChange(option.value);
                    setOpen(false);
                  }}
                  className="flex min-h-9 cursor-default select-none items-center gap-2 rounded-sm px-2 py-2 text-sm leading-5 outline-none data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-accent"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate" title={option.label}>{option.label}</span>
                    {option.description ? (
                      <span className="mt-0.5 block truncate text-xs text-muted-foreground" title={option.description}>
                        {option.description}
                      </span>
                    ) : null}
                  </span>
                  <Check className={cn('size-4 shrink-0', value === option.value ? 'opacity-100' : 'opacity-0')} />
                </Command.Item>
              ))}
            </Command.List>
          </Command>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
