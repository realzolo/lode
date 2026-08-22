'use client';

import { Tabs as GeistTabsBase } from '@geist-ui/core';
import type { ComponentType, ReactNode } from 'react';

export interface TabItem {
  value: string;
  label: ReactNode;
  content: ReactNode;
}

// @geist-ui/core's root `Tabs` export is typed as a bare ForwardRefExoticComponent
// and omits the compound `.Item`/`.Tab` members (they exist at runtime). Re-type
// to expose `Item` and accept our props. The runtime component is unaffected.
type GeistTabsType = ComponentType<{
  initialValue?: string;
  value?: string;
  onChange?: (val: string) => void;
  className?: string;
  children?: ReactNode;
}> & {
  Item: ComponentType<{ label: ReactNode; value: string; disabled?: boolean; children?: ReactNode }>;
};
const GeistTabs = GeistTabsBase as unknown as GeistTabsType;

// Thin adapter over the official Geist <Tabs> (uses Tabs.Item under the hood).
export function Tabs({
  items,
  defaultIndex = 0,
}: {
  items: TabItem[];
  defaultIndex?: number;
}) {
  const initial = items[defaultIndex]?.value ?? items[0]?.value;
  return (
    <GeistTabs initialValue={initial}>
      {items.map((item) => (
        <GeistTabs.Item key={item.value} label={item.label} value={item.value}>
          {item.content}
        </GeistTabs.Item>
      ))}
    </GeistTabs>
  );
}
