'use client';

import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { apiErrorMessage, createConnector, introspectConnector, testConnector } from '@/lib/api';

type LokiOperator = 'equals' | 'not_equals' | 'any_of' | 'not_any_of';
type LokiCondition = { kind: 'condition'; label: string; operator: LokiOperator; values: string[] };
type LokiGroup = { kind: 'group'; combinator: 'all' | 'any'; items: Array<LokiCondition | LokiGroup> };

const emptyCondition = (): LokiCondition => ({ kind: 'condition', label: '', operator: 'equals', values: [''] });
const initialFilter = (): LokiGroup => ({ kind: 'group', combinator: 'all', items: [emptyCondition()] });
const splitValues = (value: string) => value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
const hasPositiveEquals = (item: LokiCondition | LokiGroup): boolean => item.kind === 'condition'
  ? item.operator === 'equals' && Boolean(item.label.trim() && item.values[0]?.trim())
  : item.items.some(hasPositiveEquals);

export function EvidenceConnectorDialog({ open, onOpenChange, workspaceId, kinds, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; workspaceId: string; kinds: Array<{ kind: string }>; onCreated: () => Promise<void> }) {
  const t = useTranslations('workspace');
  const tc = useTranslations('common');
  const [name, setName] = useState('');
  const [kind, setKind] = useState('');
  const [endpoint, setEndpoint] = useState('');
  const [authentication, setAuthentication] = useState('bearer_token');
  const [credential, setCredential] = useState('');
  const [credentialUsername, setCredentialUsername] = useState('');
  const [verificationPath, setVerificationPath] = useState('/health');
  const [tenantId, setTenantId] = useState('');
  const [scopeValue, setScopeValue] = useState('');
  const [rootFilter, setRootFilter] = useState<LokiGroup>(initialFilter);
  const [host, setHost] = useState('');
  const [port, setPort] = useState('');
  const [database, setDatabase] = useState('');
  const [databaseUsername, setDatabaseUsername] = useState('');
  const [databasePassword, setDatabasePassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [stage, setStage] = useState<'idle' | 'creating' | 'verifying' | 'discovering'>('idle');
  const [error, setError] = useState('');
  const isEndpointConnector = ['loki', 'elasticsearch', 'opensearch', 'https'].includes(kind);
  const isDatabaseConnector = kind === 'postgresql' || kind === 'mysql';

  async function create() {
    setSubmitting(true); setStage('creating'); setError('');
    let connectorId: number | null = null;
    try {
      const created = await createConnector(workspaceId, {
        name: name.trim(), kind,
        ...(isEndpointConnector ? { endpoint: endpoint.trim() } : {}),
        ...(kind === 'loki' ? { tenant_id: tenantId || undefined, root_filter: rootFilter, authentication, credential: authentication === 'none' ? undefined : credential } : {}),
        ...(kind === 'elasticsearch' || kind === 'opensearch' ? { authentication, credential, credential_username: credentialUsername || undefined, allowed_indices: splitValues(scopeValue) } : {}),
        ...(kind === 'https' ? { authentication, credential, credential_username: credentialUsername || undefined, verification_path: verificationPath, safe_read_path: scopeValue } : {}),
        ...(isDatabaseConnector ? { host, port: port ? Number(port) : undefined, database, database_username: databaseUsername, database_password: databasePassword } : {}),
      });
      connectorId = created.id;
      setStage('verifying');
      await testConnector(workspaceId, created.id);
      setStage('discovering');
      await introspectConnector(workspaceId, created.id);
      await onCreated();
      onOpenChange(false);
    } catch (cause) {
      setError(apiErrorMessage(cause, tc('requestFailed')));
      if (connectorId !== null) await onCreated();
    } finally {
      setSubmitting(false); setStage('idle');
    }
  }

  const lokiValid = kind !== 'loki' || hasPositiveEquals(rootFilter);
  const canSubmit = Boolean(name.trim() && kind && lokiValid && (!isEndpointConnector || (endpoint && (kind === 'loki' || scopeValue) && (authentication === 'none' || credential))) && (!isDatabaseConnector || (host && database && databaseUsername && databasePassword)));
  const loadingText = stage === 'verifying' ? t('verifyingConnector') : stage === 'discovering' ? t('discoveringScope') : t('creatingConnector');

  return <Dialog open={open} onOpenChange={(value) => !submitting && onOpenChange(value)}><DialogContent variant="drawer" className="max-w-2xl overflow-hidden p-0"><DialogHeader className="border-b px-6 py-5"><DialogTitle>{t('addEvidenceConnector')}</DialogTitle></DialogHeader><div className="h-[calc(100dvh-145px)] space-y-4 overflow-y-auto px-6 py-5">
    {error ? <p role="alert" className="border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</p> : null}
    <label className="field"><span className="field-label">{t('name')}</span><Input value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label className="field"><span className="field-label">{t('connectorKind')}</span><Select value={kind} onChange={(event) => { setKind(event.target.value); setCredential(''); setScopeValue(''); setRootFilter(initialFilter()); }}><option value="">{t('connectorKind')}</option>{kinds.map((item) => <option key={item.kind} value={item.kind}>{t(`connectorKinds.${item.kind}`)}</option>)}</Select></label>
    {isEndpointConnector ? <label className="field"><span className="field-label">{t('endpoint')}</span><Input placeholder="https://service.example.com" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label> : null}
    {kind === 'loki' ? <><label className="field"><span className="field-label">{t('tenantId')}</span><Input value={tenantId} onChange={(event) => setTenantId(event.target.value)} /></label><fieldset className="space-y-2"><legend className="field-label">{t('lokiRootFilter')}</legend><LokiGroupEditor group={rootFilter} depth={1} onChange={setRootFilter} /></fieldset></> : null}
    {kind === 'elasticsearch' || kind === 'opensearch' ? <label className="field"><span className="field-label">{t('allowedIndices')}</span><Input placeholder={t('allowedIndicesPlaceholder')} value={scopeValue} onChange={(event) => setScopeValue(event.target.value)} /></label> : null}
    {kind === 'https' ? <><label className="field"><span className="field-label">{t('verificationPath')}</span><Input value={verificationPath} onChange={(event) => setVerificationPath(event.target.value)} /></label><label className="field"><span className="field-label">{t('safeReadPath')}</span><Input placeholder="/v1/events" value={scopeValue} onChange={(event) => setScopeValue(event.target.value)} /></label></> : null}
    {isEndpointConnector ? <><label className="field"><span className="field-label">{t('authentication')}</span><Select value={authentication} onChange={(event) => setAuthentication(event.target.value)}>{kind === 'loki' ? <option value="none">{t('none')}</option> : null}<option value="bearer_token">{t('bearerToken')}</option>{kind !== 'loki' ? <><option value="api_key">API Key</option><option value="basic">{t('basicAuth')}</option></> : null}</Select></label>{authentication === 'basic' ? <label className="field"><span className="field-label">{t('username')}</span><Input value={credentialUsername} onChange={(event) => setCredentialUsername(event.target.value)} /></label> : null}{authentication !== 'none' ? <label className="field"><span className="field-label">{authentication === 'api_key' ? 'API Key' : t('accessToken')}</span><Input type="password" value={credential} onChange={(event) => setCredential(event.target.value)} /></label> : null}</> : null}
    {isDatabaseConnector ? <><p className="text-sm text-muted-foreground">{t('databaseAutoDiscoveryDescription')}</p><div className="grid gap-3 sm:grid-cols-2"><label className="field"><span className="field-label">{t('databaseHost')}</span><Input value={host} onChange={(event) => setHost(event.target.value)} /></label><label className="field"><span className="field-label">{t('databasePort')}</span><Input inputMode="numeric" value={port} onChange={(event) => setPort(event.target.value)} /></label><label className="field"><span className="field-label">{t('databaseName')}</span><Input value={database} onChange={(event) => setDatabase(event.target.value)} /></label><label className="field"><span className="field-label">{t('username')}</span><Input value={databaseUsername} onChange={(event) => setDatabaseUsername(event.target.value)} /></label></div><label className="field"><span className="field-label">{t('password')}</span><Input type="password" value={databasePassword} onChange={(event) => setDatabasePassword(event.target.value)} /></label></> : null}
  </div><DialogFooter className="border-t px-6 py-4"><Button variant="outline" disabled={submitting} onClick={() => onOpenChange(false)}>{tc('cancel')}</Button><Button variant="primary" loading={submitting} loadingText={loadingText} disabled={!canSubmit} onClick={() => void create()}>{t('create')}</Button></DialogFooter></DialogContent></Dialog>;
}

function LokiGroupEditor({ group, depth, onChange, removable = false, onRemove }: { group: LokiGroup; depth: number; onChange: (group: LokiGroup) => void; removable?: boolean; onRemove?: () => void }) {
  const t = useTranslations('workspace');
  function update(index: number, item: LokiCondition | LokiGroup) { const items = [...group.items]; items[index] = item; onChange({ ...group, items }); }
  function remove(index: number) { onChange({ ...group, items: group.items.filter((_, itemIndex) => itemIndex !== index) }); }
  return <div className="space-y-2 border p-3"><div className="flex items-center justify-between gap-2"><Select className="w-28" value={group.combinator} onChange={(event) => onChange({ ...group, combinator: event.target.value as 'all' | 'any' })}><option value="all">{t('matchAll')}</option><option value="any">{t('matchAny')}</option></Select>{removable ? <Button size="icon" variant="ghost" title={t('removeGroup')} aria-label={t('removeGroup')} onClick={onRemove}><Trash2 size={15} /></Button> : null}</div>{group.items.map((item, index) => item.kind === 'condition' ? <div key={index} className="grid gap-2 sm:grid-cols-[1fr_140px_1fr_32px]"><Input placeholder={t('labelName')} value={item.label} onChange={(event) => update(index, { ...item, label: event.target.value })} /><Select value={item.operator} onChange={(event) => update(index, { ...item, operator: event.target.value as LokiOperator })}><option value="equals">{t('equals')}</option><option value="not_equals">{t('notEquals')}</option><option value="any_of">{t('anyOf')}</option><option value="not_any_of">{t('notAnyOf')}</option></Select><Input placeholder={t('labelValues')} value={item.values.join(', ')} onChange={(event) => update(index, { ...item, values: splitValues(event.target.value) })} /><Button size="icon" variant="ghost" title={t('removeCondition')} aria-label={t('removeCondition')} disabled={group.items.length === 1} onClick={() => remove(index)}><Trash2 size={15} /></Button></div> : <LokiGroupEditor key={index} group={item} depth={depth + 1} removable onRemove={() => remove(index)} onChange={(value) => update(index, value)} />)}<div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => onChange({ ...group, items: [...group.items, emptyCondition()] })}><Plus size={14} />{t('addCondition')}</Button>{depth < 3 ? <Button size="sm" variant="outline" onClick={() => onChange({ ...group, items: [...group.items, { kind: 'group', combinator: 'all', items: [emptyCondition()] }] })}><Plus size={14} />{t('addGroup')}</Button> : null}</div></div>;
}
