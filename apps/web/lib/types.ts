// Shared view-layer types. These mirror the backend SQLAlchemy models
// (alerts / analyses / application_* / memories) so the UI can be wired to
// real API responses later without changing component contracts.

export type Level = 'CRITICAL' | 'WARNING';

export type AnalysisStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'needs_human';

export type StepStatus = 'done' | 'running' | 'pending';

export interface Application {
  id: string;
  name: string;
  topic: string;
  level: Level;
}

export interface Analysis {
  dedupeKey: string;
  applicationId: string;
  title: string;
  level: Level;
  status: AnalysisStatus;
  confidence: number | null;
  conclusion: string | null;
}

export interface AnalysisStep {
  nodeType:
    | 'receive'
    | 'git_sync'
    | 'context'
    | 'ai_analysis'
    | 'memory'
    | 'conclusion';
  title: string;
  status: StepStatus;
  detail?: string;
}

export interface Memory {
  id: string;
  triggerSignature: string;
  content: string;
  valid: boolean;
}
