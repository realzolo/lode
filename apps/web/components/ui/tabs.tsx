'use client';

import { ReactNode, useState } from 'react';
import { cx } from '@/lib/cn';

export interface TabItem {
  value: string;
  label: ReactNode;
  content: ReactNode;
}

export function Tabs({
  items,
  defaultIndex = 0,
}: {
  items: TabItem[];
  defaultIndex?: number;
}) {
  const [active, setActive] = useState(defaultIndex);

  return (
    <div>
      <div className="tabs" role="tablist">
        {items.map((item, i) => (
          <button
            key={item.value}
            role="tab"
            aria-selected={i === active}
            className={cx('tab', i === active && 'active')}
            onClick={() => setActive(i)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">{items[active].content}</div>
    </div>
  );
}
