'use client';

import { Check, X } from 'lucide-react';
import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { apiErrorMessage, createIncidentAction } from '@/lib/api';
import type { ActionProposal, IncidentAction, IncidentOverview, WorkspaceMember } from '@/lib/types';

export function ActionProposalSection({
  proposals,
  members,
  onDecide,
}: {
  proposals: ActionProposal[];
  members: WorkspaceMember[];
  onDecide: (proposal: ActionProposal, decision: 'accept' | 'reject', owner: number | null, reason: string) => Promise<void>;
}) {
  const t = useTranslations('workbench');
  const [owner, setOwner] = useState('');
  const [reason, setReason] = useState('');

  return <section className="dashboard-section space-y-3">
    <h2 className="dashboard-section-title">{t('actionProposals')}</h2>
    {proposals.length === 0 ? <EmptyState compact title={t('noActionProposals')} /> : <div className="dashboard-record-list">
      {proposals.map((proposal) => <article key={proposal.id} className="dashboard-record">
        <div className="flex flex-wrap justify-between gap-2">
          <strong>{proposal.title}</strong>
          <span className="text-sm text-muted-foreground">{proposal.priority} · {proposal.decision || t('pendingDecision')}</span>
        </div>
        <p className="mt-2 text-sm">{proposal.rationale}</p>
        {proposal.decision === null && <>
          <div className="dashboard-inline-fields mt-3">
            <label className="field">
              <span className="field-label">{t('actionOwner')}</span>
              <Select value={owner} onChange={(event) => setOwner(event.target.value)}>
                <option value="">{t('actionOwner')}</option>
                {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
              </Select>
            </label>
            <label className="field">
              <span className="field-label">{t('decisionReason')}</span>
              <Input value={reason} onChange={(event) => setReason(event.target.value)} />
            </label>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" disabled={!owner || !reason.trim()} onClick={() => void onDecide(proposal, 'accept', Number(owner), reason.trim())}><Check size={14} />{t('accept')}</Button>
            <Button size="sm" variant="outline" disabled={!reason.trim()} onClick={() => void onDecide(proposal, 'reject', null, reason.trim())}><X size={14} />{t('reject')}</Button>
          </div>
        </>}
      </article>)}
    </div>}
  </section>;
}

export function IncidentActionSection({
  actions,
  members,
  onUpdate,
}: {
  actions: IncidentAction[];
  members: WorkspaceMember[];
  onUpdate: (action: IncidentAction, status: IncidentAction['status'], owner: number) => Promise<void>;
}) {
  const t = useTranslations('workbench');

  return <section className="dashboard-section space-y-3">
    <h2 className="dashboard-section-title">{t('followUpActions')}</h2>
    {actions.length === 0 ? <EmptyState compact title={t('noActions')} /> : <div className="dashboard-record-list">
      {actions.map((action) => <article key={action.id} className="dashboard-record">
        <div className="flex flex-wrap justify-between gap-2"><strong>{action.title}</strong><span className="text-sm text-muted-foreground">{action.priority}</span></div>
        <p className="mt-2 text-sm">{action.rationale}</p>
        <div className="dashboard-inline-fields dashboard-inline-fields-equal mt-3">
          <label className="field">
            <span className="field-label">{t('actionStatus')}</span>
            <Select value={action.status} onChange={(event) => void onUpdate(action, event.target.value as IncidentAction['status'], action.owner_id)}>
              {(['open', 'in_progress', 'blocked', 'validation', 'completed', 'cancelled'] as const).map((status) => <option key={status} value={status}>{t(`actionStatuses.${status}`)}</option>)}
            </Select>
          </label>
          <label className="field">
            <span className="field-label">{t('actionOwner')}</span>
            <Select value={String(action.owner_id)} onChange={(event) => void onUpdate(action, action.status, Number(event.target.value))}>
              {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
            </Select>
          </label>
        </div>
      </article>)}
    </div>}
  </section>;
}

export function CreateActionDialog({
  open,
  onOpenChange,
  incident,
  members,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  incident: IncidentOverview;
  members: WorkspaceMember[];
  onCreated: () => Promise<void>;
}) {
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
      await createIncidentAction(incident.id, {
        action_type: actionType,
        priority,
        title,
        rationale,
        validation,
        owner_id: Number(owner),
        investigation_id: incident.investigations[0]?.id ?? null,
        evidence_refs: [],
      });
      await onCreated();
      onOpenChange(false);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
    } finally {
      setSaving(false);
    }
  }

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent variant="drawer">
      <DialogHeader><DialogTitle>{t('createAction')}</DialogTitle></DialogHeader>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="field">
          <span className="field-label">{t('actionType')}</span>
          <Select value={actionType} onChange={(event) => setActionType(event.target.value as typeof actionType)}>
            <option value="mitigate">{t('actionTypeMitigate')}</option>
            <option value="remediate">{t('actionTypeRemediate')}</option>
            <option value="validate">{t('actionTypeValidate')}</option>
            <option value="prevent">{t('actionTypePrevent')}</option>
          </Select>
        </label>
        <label className="field">
          <span className="field-label">{t('priority')}</span>
          <Select value={priority} onChange={(event) => setPriority(event.target.value as typeof priority)}>
            {(['P0', 'P1', 'P2', 'P3'] as const).map((value) => <option key={value} value={value}>{value}</option>)}
          </Select>
        </label>
        <label className="field sm:col-span-2">
          <span className="field-label">{t('actionOwner')}</span>
          <Select value={owner} onChange={(event) => setOwner(event.target.value)}>
            <option value="">{t('actionOwner')}</option>
            {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
          </Select>
        </label>
        <label className="field sm:col-span-2">
          <span className="field-label">{t('titleField')}</span>
          <Input value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label className="field sm:col-span-2">
          <span className="field-label">{t('rationale')}</span>
          <Textarea value={rationale} onChange={(event) => setRationale(event.target.value)} />
        </label>
        <label className="field sm:col-span-2">
          <span className="field-label">{t('validation')}</span>
          <Textarea value={validation} onChange={(event) => setValidation(event.target.value)} />
        </label>
      </div>
      {error && <p className="dashboard-feedback" role="alert">{error}</p>}
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>{tc('cancel')}</Button>
        <Button variant="primary" loading={saving} disabled={!owner || !title || !rationale || !validation} onClick={() => void create()}>{tc('save')}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
