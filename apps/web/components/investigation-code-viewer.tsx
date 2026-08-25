'use client';

import dynamic from 'next/dynamic';
import { FileCode2, GitCompareArrows } from 'lucide-react';
import type { InvestigationDetail } from '@/lib/api';

const Editor = dynamic(() => import('@monaco-editor/react').then((module) => module.Editor), { ssr: false, loading: () => <div className="code-viewer-loading">正在加载代码视图...</div> });
const DiffEditor = dynamic(() => import('@monaco-editor/react').then((module) => module.DiffEditor), { ssr: false, loading: () => <div className="code-viewer-loading">正在加载差异视图...</div> });

type Evidence = InvestigationDetail['evidence'][number];

export function InvestigationCodeViewer({ evidence }: { evidence: Evidence | null }) {
  if (!evidence?.code) return null;
  const options = { readOnly: true, domReadOnly: true, minimap: { enabled: false }, scrollBeyondLastLine: false, lineNumbersMinChars: 3, fontSize: 13, lineHeight: 20, wordWrap: 'on' as const, accessibilitySupport: 'on' as const };
  const label = evidence.locator?.split(':').slice(-2, -1)[0] || evidence.type;
  return <section className="code-viewer" aria-label={`代码证据 ${label}`}>
    <header><span>{evidence.code.mode === 'diff' ? <GitCompareArrows size={15} /> : <FileCode2 size={15} />}{evidence.code.mode === 'diff' ? '源码差异' : '源码证据'}</span><code>{label}</code></header>
    {evidence.code.mode === 'source'
      ? <Editor height="360px" language={evidence.code.language} value={evidence.code.content} options={{ ...options, lineNumbers: 'on' }} />
      : <DiffEditor height="420px" language={evidence.code.language} original={evidence.code.before} modified={evidence.code.after} options={{ ...options, renderSideBySide: true }} />}
  </section>;
}
