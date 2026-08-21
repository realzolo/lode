import {
  TableHTMLAttributes,
  HTMLAttributes,
  TdHTMLAttributes,
  ThHTMLAttributes,
} from 'react';
import { cx } from '@/lib/cn';

export function Table({ className, ...props }: TableHTMLAttributes<HTMLTableElement>) {
  return <table className={cx('table', className)} {...props} />;
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
