'use client';

import dynamic from 'next/dynamic';
import { FileCode2, GitCompareArrows } from 'lucide-react';
import type { InvestigationDetail } from '@/lib/api';

const Editor = dynamic(() => import('@monaco-editor/react').then((module) => module.Editor), { ssr: false, loading: () => <div className="code-viewer-loading">正在加载代码视图...</div> });
const DiffEditor = dynamic(() => import('@monaco-editor/react').then((module) => module.DiffEditor), { ssr: false, loading: () => <div className="code-viewer-loading">正在加载差异视图...</div> });

type Evidence = InvestigationDetail['evidence'][number];

export function InvestigationCodeViewer({ evidence }: { evidence: Evidence | null }) {
  if (!evidence?.code) return null;
  const code = evidence.code;
  const options = { readOnly: true, domReadOnly: true, minimap: { enabled: false }, scrollBeyondLastLine: false, lineNumbersMinChars: 3, fontSize: 13, lineHeight: 20, wordWrap: 'on' as const, accessibilitySupport: 'on' as const };
  const label = code.mode === 'source' ? code.anchor.path : evidence.type;
  return <section className="code-viewer" aria-label={`代码证据 ${label}`}>
    <header><span>{code.mode === 'diff' ? <GitCompareArrows size={15} /> : <FileCode2 size={15} />}{code.mode === 'diff' ? '源码差异' : '源码证据'}</span><code>{code.mode === 'source' ? `${label}@${code.anchor.revision.slice(0, 12)}` : `${code.revisions.incident.slice(0, 12)}..${code.revisions.latest.slice(0, 12)}`}</code></header>
    {code.mode === 'source'
      ? <Editor height="420px" language={code.language} value={code.content} options={{ ...options, lineNumbers: (value) => String(code.anchor.snippet_start_line + value - 1) }} onMount={(editor, monaco) => {
        const line = code.highlight_line;
        editor.revealLineInCenter(line);
        editor.deltaDecorations([], [{ range: new monaco.Range(line, 1, line, 1), options: { isWholeLine: true, className: 'evidence-code-highlight' } }]);
      }} />
      : <DiffEditor height="420px" language={code.language} original={code.before} modified={code.after} options={{ ...options, renderSideBySide: true }} />}
  </section>;
}
