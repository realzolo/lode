'use client';

import { Check, X } from 'lucide-react';
import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { apiErrorMessage, createIncidentAction } from '@/lib/api';
import type { ActionProposal, IncidentAction, IncidentOverview, WorkspaceMember } from '@/lib/types';

export function ActionProposalSection({ proposals, members, onDecide }: { proposals: ActionProposal[]; members: WorkspaceMember[]; onDecide: (proposal: ActionProposal, decision: 'accept' | 'reject', owner: number | null, reason: string) => Promise<void> }) {
  const t = useTranslations('workbench');
  const [owner, setOwner] = useState('');
  const [reason, setReason] = useState('');
  return <section className="space-y-3 border-t pt-6"><h2 className="text-base font-semibold">{t('actionProposals')}</h2>{proposals.length === 0 ? <p className="text-sm text-muted-foreground">{t('noActionProposals')}</p> : <div className="divide-y border-y">{proposals.map((proposal) => <article key={proposal.id} className="py-3"><div className="flex flex-wrap justify-between gap-2"><strong>{proposal.title}</strong><span className="text-sm text-muted-foreground">{proposal.priority} · {proposal.decision || t('pendingDecision')}</span></div><p className="mt-2 text-sm">{proposal.rationale}</p>{proposal.decision === null && <div className="mt-3 flex flex-wrap gap-2"><Select className="w-48" value={owner} onChange={(event) => setOwner(event.target.value)}><option value="">{t('actionOwner')}</option>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</Select><Input className="min-w-56 flex-1" placeholder={t('decisionReason')} value={reason} onChange={(event) => setReason(event.target.value)} /><Button size="sm" disabled={!owner || !reason.trim()} onClick={() => void onDecide(proposal, 'accept', Number(owner), reason.trim())}><Check size={14} />{t('accept')}</Button><Button size="sm" variant="outline" disabled={!reason.trim()} onClick={() => void onDecide(proposal, 'reject', null, reason.trim())}><X size={14} />{t('reject')}</Button></div>}</article>)}</div>}</section>;
}

export function IncidentActionSection({ actions, members, onUpdate }: { actions: IncidentAction[]; members: WorkspaceMember[]; onUpdate: (action: IncidentAction, status: IncidentAction['status'], owner: number) => Promise<void> }) {
  const t = useTranslations('workbench');
  return <section className="space-y-3 border-t pt-6">
    <h2 className="text-base font-semibold">{t('followUpActions')}</h2>
    {actions.length === 0 ? <p className="text-sm text-muted-foreground">{t('noActions')}</p> : <div className="divide-y border-y">
      {actions.map((action) => <article key={action.id} className="py-3">
        <div className="flex flex-wrap justify-between gap-2"><strong>{action.title}</strong><span className="text-sm text-muted-foreground">{action.priority}</span></div>
        <p className="mt-2 text-sm">{action.rationale}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Select className="w-44" value={action.status} onChange={(event) => void onUpdate(action, event.target.value as IncidentAction['status'], action.owner_id)}>{(['open', 'in_progress', 'blocked', 'validation', 'completed', 'cancelled'] as const).map((status) => <option key={status} value={status}>{t(`actionStatuses.${status}`)}</option>)}</Select>
          <Select className="w-48" value={String(action.owner_id)} onChange={(event) => void onUpdate(action, action.status, Number(event.target.value))}>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</Select>
        </div>
      </article>)}
    </div>}
  </section>;
}

export function CreateActionDialog({ open, onOpenChange, incident, members, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; incident: IncidentOverview; members: WorkspaceMember[]; onCreated: () => Promise<void> }) {
  const t = useTranslations('workbench');
  const tc = useTranslations('common');
  const [actionType, setActionType] = useState<'mitigate' | 'remediate' | 'validate' | 'prevent'>('remediate');
  const [priority, setPriority] = useState<'P0' | 'P1' | 'P2' | 'P3'>('P2');
  const [title, setTitle] = useState('');
  const [rationale, setRationale] = useState('');
  const [validation, setValidation] = useState('');
  const [owner, setOwner] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  async function create() {
    setSaving(true);
    try {
      await createIncidentAction(incident.id, { action_type: actionType, priority, title, rationale, validation, owner_id: Number(owner), investigation_id: incident.investigations[0]?.id ?? null, evidence_refs: [] });
      await onCreated();
      onOpenChange(false);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setSaving(false);
    }
  }

  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent variant="drawer"><DialogHeader><DialogTitle>{t('createAction')}</DialogTitle></DialogHeader><div className="grid gap-3 sm:grid-cols-2"><Select value={actionType} onChange={(event) => setActionType(event.target.value as typeof actionType)}><option value="mitigate">{t('actionTypeMitigate')}</option><option value="remediate">{t('actionTypeRemediate')}</option><option value="validate">{t('actionTypeValidate')}</option><option value="prevent">{t('actionTypePrevent')}</option></Select><Select value={priority} onChange={(event) => setPriority(event.target.value as typeof priority)}>{(['P0', 'P1', 'P2', 'P3'] as const).map((value) => <option key={value}>{value}</option>)}</Select><Select className="sm:col-span-2" value={owner} onChange={(event) => setOwner(event.target.value)}><option value="">{t('actionOwner')}</option>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</Select><Input className="sm:col-span-2" placeholder={t('titleField')} value={title} onChange={(event) => setTitle(event.target.value)} /><Textarea className="sm:col-span-2" placeholder={t('rationale')} value={rationale} onChange={(event) => setRationale(event.target.value)} /><Textarea className="sm:col-span-2" placeholder={t('validation')} value={validation} onChange={(event) => setValidation(event.target.value)} /></div>{error && <p className="text-sm text-destructive">{error}</p>}<DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={saving} disabled={!owner || !title || !rationale || !validation} onClick={() => void create()}>{tc('save')}</Button></DialogFooter></DialogContent></Dialog>;
}
