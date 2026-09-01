import {
  TableHTMLAttributes,
  HTMLAttributes,
  TdHTMLAttributes,
  ThHTMLAttributes,
} from 'react';
import { cx } from '@/lib/cn';

export function Table({ className, ...props }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="table-wrap">
      <table className={cx('table', className)} {...props} />
    </div>
  );
}

export function THead(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead {...props} />;
}

export function TBody(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody {...props} />;
}

export function Tr(props: HTMLAttributes<HTMLTableRowElement>) {
  return <tr {...props} />;
}

export function Th(props: ThHTMLAttributes<HTMLTableCellElement>) {
  return <th {...props} />;
}

export function Td(props: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td {...props} />;
}

type TableColumnsProps = {
  widths: number[];
  leadingWidth?: number;
  trailingWidth?: number;
};

export function TableColumns({ widths, leadingWidth = 0, trailingWidth = 0 }: TableColumnsProps) {
  const total = widths.reduce((sum, width) => sum + width, 0);
  const fixedWidth = leadingWidth + trailingWidth;
  const contentWidth = (width: number) => {
    const ratio = width / total;
    const percentage = Number((ratio * 100).toFixed(4));
    const fixedShare = Number((fixedWidth * ratio).toFixed(4));

    return fixedWidth
      ? `calc(${percentage}% - ${fixedShare}px)`
      : `${percentage}%`;
  };

  return <colgroup>
    {leadingWidth ? <col style={{ width: `${leadingWidth}px` }} /> : null}
    {widths.map((width, index) => <col key={index} style={{ width: contentWidth(width) }} />)}
    {trailingWidth ? <col style={{ width: `${trailingWidth}px` }} /> : null}
  </colgroup>;
}
