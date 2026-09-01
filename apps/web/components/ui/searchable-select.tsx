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
  onSearchChange?: (query: string) => void;
  hasMore?: boolean;
  onLoadMore?: () => void;
  loadMoreLabel?: string;
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
  onSearchChange,
  hasMore = false,
  onLoadMore,
  loadMoreLabel,
}: SearchableSelectProps) {
  const [open, setOpen] = React.useState(false);
  const listboxId = React.useId();
  const selected = options.find((option) => option.value === value);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          role="combobox"
          aria-label={ariaLabel}
          aria-controls={listboxId}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-busy={loading || undefined}
          aria-disabled={loading || undefined}
          disabled={disabled}
          onPointerDown={(event) => {
            if (!loading) return;
            event.preventDefault();
            event.stopPropagation();
          }}
          onClick={(event) => {
            if (!loading) return;
            event.preventDefault();
            event.stopPropagation();
          }}
          onKeyDown={(event) => {
            if (!loading || ![' ', 'ArrowDown', 'Enter'].includes(event.key)) return;
            event.preventDefault();
            event.stopPropagation();
          }}
          className="select-trigger flex h-9 w-full min-w-0 items-center justify-between gap-2 px-3 text-left text-sm outline-none transition disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-wait aria-disabled:opacity-50"
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
          id={listboxId}
          align="start"
          sideOffset={6}
          className="z-[70] w-[var(--radix-popover-trigger-width)] min-w-64 max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-[var(--dashboard-border)] bg-[var(--dashboard-panel)] text-popover-foreground shadow-elevation-4"
        >
          <Command className="w-full" filter={(optionValue, search, keywords) => {
            const haystack = `${optionValue} ${(keywords || []).join(' ')}`.toLowerCase();
            return haystack.includes(search.trim().toLowerCase()) ? 1 : 0;
          }}>
            <div className="flex h-10 items-center gap-2 border-b border-[var(--dashboard-border)] px-3">
              <Search className="size-4 shrink-0 text-muted-foreground" />
              <Command.Input
                autoFocus
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder={searchPlaceholder}
                onValueChange={onSearchChange}
                className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
            </div>
            <Command.List role="listbox" aria-label={ariaLabel} className="max-h-64 overflow-y-auto p-1">
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
                  className="flex min-h-8 cursor-default select-none items-center gap-2 rounded-md px-2 py-2 text-sm leading-5 outline-none data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-[var(--dashboard-hover)]"
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
            {hasMore && onLoadMore && loadMoreLabel ? (
              <div className="border-t border-[var(--dashboard-border)] p-1">
                <button
                  type="button"
                  aria-busy={loading || undefined}
                  aria-disabled={loading || undefined}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-link hover:bg-[var(--dashboard-hover)] focus-visible:outline-none focus-visible:shadow-geist-focus aria-disabled:cursor-wait aria-disabled:opacity-50"
                  onClick={(event) => {
                    if (loading) {
                      event.preventDefault();
                      return;
                    }
                    onLoadMore();
                  }}
                >
                  {loading ? <LoaderCircle className="size-4 shrink-0 animate-spin" aria-hidden="true" /> : null}
                  {loadMoreLabel}
                </button>
              </div>
            ) : null}
          </Command>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
