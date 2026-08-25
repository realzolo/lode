'use client';

import { useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { FileCode2 } from 'lucide-react';
import type { OnMount } from '@monaco-editor/react';
import { useTheme } from 'next-themes';
import type { InvestigationEvidence } from '@/lib/api';

const Editor = dynamic(() => import('@monaco-editor/react').then((module) => module.Editor), { ssr: false, loading: () => <div className="code-viewer-loading">正在加载代码...</div> });

export function InvestigationCodeViewer({ evidence, range }: { evidence: InvestigationEvidence | null; range?: { start: number; end: number } | null }) {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const decoration = useRef<string[]>([]);
  useEffect(() => setMounted(true), []);
  if (!evidence?.code) return null;
  const code = evidence.code;
  const relativeStart = Math.max(1, (range?.start ?? code.anchor.start_line) - code.anchor.start_line + 1);
  const relativeEnd = Math.max(relativeStart, (range?.end ?? code.anchor.end_line) - code.anchor.start_line + 1);
  const onMount: OnMount = (editor, monaco) => {
    decoration.current = editor.deltaDecorations(decoration.current, [{ range: new monaco.Range(relativeStart, 1, relativeEnd, 1), options: { isWholeLine: true, className: 'code-finding-line', linesDecorationsClassName: 'code-finding-gutter' } }]);
    editor.revealLineInCenter(relativeStart);
  };
  return <section className="code-viewer" aria-label={`代码位置 ${code.anchor.path}`}>
    <header><span><FileCode2 size={15} />{code.anchor.path}</span><code>{code.anchor.revision.slice(0, 12)} · {code.anchor.symbol || '未识别符号'} · L{range?.start ?? code.anchor.start_line}-L{range?.end ?? code.anchor.end_line}</code></header>
    {mounted ? <Editor key={`${evidence.id}-${relativeStart}-${relativeEnd}`} height="420px" language={code.language} value={code.content} theme={resolvedTheme === 'light' ? 'light' : 'vs-dark'} onMount={onMount} options={{ readOnly: true, domReadOnly: true, minimap: { enabled: false }, scrollBeyondLastLine: false, lineNumbers: (line) => String(code.anchor.start_line + line - 1), lineNumbersMinChars: 4, fontSize: 14, lineHeight: 22, wordWrap: 'off', accessibilitySupport: 'on', automaticLayout: true, renderLineHighlight: 'none', padding: { top: 10, bottom: 10 } }} /> : <div className="code-viewer-loading" aria-hidden="true" />}
  </section>;
}
