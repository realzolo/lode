'use client';

import * as React from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { cn } from '@/lib/utils';

export interface TabItem {
  value: string;
  label: React.ReactNode;
  content: React.ReactNode;
}

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      'flex h-[50px] w-full max-w-full items-center justify-start gap-6 overflow-x-auto border-b border-[var(--dashboard-border)] bg-transparent p-0 text-muted-foreground',
      className,
    )}
    {...props}
  />
));
TabsList.displayName = TabsPrimitive.List.displayName;

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      'inline-flex -mb-px h-[50px] shrink-0 items-center justify-center whitespace-nowrap border-b-2 border-transparent px-0.5 text-sm font-normal transition-colors hover:text-[var(--color-10)] focus-visible:outline-none focus-visible:shadow-geist-focus disabled:pointer-events-none disabled:opacity-50 data-[state=active]:border-[var(--color-10)] data-[state=active]:text-[var(--color-10)]',
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      'mt-5 focus-visible:outline-none focus-visible:shadow-geist-focus data-[state=active]:animate-in data-[state=active]:fade-in-0 data-[state=active]:duration-200',
      className,
    )}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;

// Current item-based API shared by control-plane and investigation pages.
export function Tabs({
  items,
  defaultIndex = 0,
  value,
  ariaLabel,
  onValueChange,
}: {
  items: TabItem[];
  defaultIndex?: number;
  value?: string;
  ariaLabel?: string;
  onValueChange?: (value: string) => void;
}) {
  const initial = items[defaultIndex]?.value ?? items[0]?.value;
  return (
    <TabsPrimitive.Root value={value} defaultValue={value === undefined ? initial : undefined} className="w-full" onValueChange={onValueChange}>
      <TabsList aria-label={ariaLabel}>
        {items.map((item) => (
          <TabsTrigger key={item.value} value={item.value}>
            {item.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {items.map((item) => (
        <TabsContent key={item.value} value={item.value}>
          {item.content}
        </TabsContent>
      ))}
    </TabsPrimitive.Root>
  );
}

export { TabsList, TabsTrigger, TabsContent };
