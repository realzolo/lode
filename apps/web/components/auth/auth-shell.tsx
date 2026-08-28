import type { ReactNode } from 'react';
import { LodeLogo, LodeMark } from '@/components/brand/lode-logo';

interface AuthShellProps {
  appName: string;
  children: ReactNode;
  descriptor: string;
}

export function AuthShell({ appName, children, descriptor }: AuthShellProps) {
  return (
    <main className="auth-shell">
      <aside className="auth-visual">
        <header className="auth-visual-brand">
          <LodeLogo name={appName} />
        </header>

        <div className="auth-visual-center" aria-hidden="true">
          <div className="auth-analysis-frame">
            <span className="auth-analysis-boundary auth-analysis-boundary-outer" />
            <span className="auth-analysis-boundary auth-analysis-boundary-inner" />
            <span className="auth-analysis-axis auth-analysis-axis-x" />
            <span className="auth-analysis-axis auth-analysis-axis-y" />
            <span className="auth-analysis-node auth-analysis-node-north" />
            <span className="auth-analysis-node auth-analysis-node-east" />
            <span className="auth-analysis-node auth-analysis-node-south" />
            <span className="auth-analysis-node auth-analysis-node-west" />
            <LodeMark className="auth-analysis-mark" />
          </div>
        </div>

        <p className="auth-visual-caption">{descriptor}</p>
      </aside>

      <section className="auth-main">
        <header className="auth-mobile-header">
          <LodeLogo name={appName} />
        </header>
        <div className="auth-stage">{children}</div>
        <footer className="auth-footer">{descriptor}</footer>
      </section>
    </main>
  );
}
