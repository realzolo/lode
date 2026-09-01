"use client";

import { AlertTriangle } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import type {
  CausalGraph,
  CausalNode,
  ConfigurationAssessmentView,
  InvestigationReportView,
  SourceAssessmentView,
} from "@/lib/types";

type EvidenceHandler = (id: number) => void;

function identityStatusLabel(status: string, t: ReturnType<typeof useTranslations>) {
  const key = ["verified", "provisional", "ambiguous"].includes(status)
    ? status
    : "unknown";
  return t(`identityStatuses.${key}`);
}

function causalStatusLabel(
  status: CausalNode["status"],
  t: ReturnType<typeof useTranslations>,
) {
  return t(`causalStatuses.${status}`);
}

export function IncidentReportPanel({
  report,
  onEvidence,
}: {
  report: InvestigationReportView;
  onEvidence: EvidenceHandler;
}) {
  const t = useTranslations("workbench");
  const tc = useTranslations("common");
  const locale = useLocale();
  const dateLocale = locale === "zh" ? "zh-CN" : "en-US";
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">{report.headline}</h3>
        <p className="mt-2 max-w-4xl text-sm leading-6">
          {report.executive_summary}
        </p>
      </div>
      {report.impact_scope.length > 0 && (
        <EvidenceStatementList
          title={t("impactScope")}
          items={report.impact_scope}
          onEvidence={onEvidence}
        />
      )}
      <CausalGraphPanel graph={report.causal_graph} onEvidence={onEvidence} />
      {report.participants.length > 0 && (
        <section>
          <h3 className="font-semibold">{t("participants")}</h3>
          <div className="dashboard-record-list mt-2">
            {report.participants.map((item) => (
              <div
                key={item.entity_ref}
                className="dashboard-record flex flex-wrap items-center justify-between gap-2 text-sm"
              >
                <span>
                  {item.display_name} · {identityStatusLabel(item.identity_status, t)}
                </span>
                <EvidenceRefs
                  values={item.evidence_refs}
                  onSelect={onEvidence}
                />
              </div>
            ))}
          </div>
        </section>
      )}
      {report.timeline_summary.length > 0 && (
        <section>
          <h3 className="font-semibold">{t("reportTimeline")}</h3>
          <div className="dashboard-record-list mt-2">
            {report.timeline_summary.map((item) => (
              <div key={item.event_ref} className="dashboard-record text-sm">
                <span className="text-xs text-muted-foreground">
                  {new Date(item.occurred_at).toLocaleString(dateLocale)}
                </span>
                <p>{item.summary}</p>
                <EvidenceRefs
                  values={item.evidence_refs}
                  onSelect={onEvidence}
                />
              </div>
            ))}
          </div>
        </section>
      )}
      {report.counter_evidence.length > 0 && (
        <EvidenceStatementList
          title={t("counterEvidence")}
          items={report.counter_evidence}
          onEvidence={onEvidence}
        />
      )}
      {report.evidence_gaps.length > 0 && (
        <section>
          <h3 className="font-semibold">{t("evidenceGaps")}</h3>
          <div className="dashboard-record-list mt-2">
            {report.evidence_gaps.map((gap) => (
              <div
                key={gap.description}
                className="dashboard-record flex gap-2 text-sm"
              >
                <AlertTriangle
                  className="mt-0.5 shrink-0 text-warning"
                  size={15}
                />
                <div>
                  <strong>{gap.description}</strong>
                  <p className="mt-1 text-muted-foreground">
                    {gap.consequence}
                  </p>
                  <p className="mt-1">{gap.required_evidence}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      {report.action_recommendations.length > 0 && (
        <section>
          <h3 className="font-semibold">{t("recommendedNextStep")}</h3>
          <div className="dashboard-record-list mt-2">
            {report.action_recommendations.map((item) => (
              <article
                key={`${item.action_type}:${item.title}`}
                className="dashboard-record"
              >
                <div className="flex justify-between gap-2">
                  <strong>{item.title}</strong>
                  <span className="text-xs text-muted-foreground">
                    {item.priority.toUpperCase()}
                  </span>
                </div>
                <p className="mt-1 text-sm">{item.rationale}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {item.validation}
                </p>
                <EvidenceRefs
                  values={item.evidence_refs}
                  onSelect={onEvidence}
                />
              </article>
            ))}
          </div>
        </section>
      )}
      {report.code_findings.length > 0 && (
        <section>
          <h3 className="font-semibold">{t("codeFindings")}</h3>
          <div className="dashboard-record-list mt-2">
            {report.code_findings.map((finding) => (
              <article key={finding.id} className="dashboard-record">
                <strong>
                  {finding.path || tc("empty")}
                  {finding.symbol ? ` · ${finding.symbol}` : ""}
                </strong>
                <p className="mt-2 text-sm">{finding.faulty_behavior}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {finding.why_wrong}
                </p>
                <EvidenceRefs
                  values={[
                    ...finding.incident_evidence_refs,
                    ...finding.supporting_evidence_refs,
                    ...finding.counter_evidence_refs,
                  ]}
                  onSelect={onEvidence}
                />
              </article>
            ))}
          </div>
        </section>
      )}
      {report.source_assessments.length > 0 && (
        <SourceAssessmentList
          items={report.source_assessments}
          onEvidence={onEvidence}
        />
      )}
      {report.configuration_assessments.length > 0 && (
        <ConfigurationAssessmentList
          items={report.configuration_assessments}
          onEvidence={onEvidence}
        />
      )}
    </div>
  );
}

function EvidenceStatementList({
  title,
  items,
  onEvidence,
}: {
  title: string;
  items: Array<{ text: string; evidence_refs: number[] }>;
  onEvidence: EvidenceHandler;
}) {
  return (
    <section>
      <h3 className="font-semibold">{title}</h3>
      <ul className="mt-2 space-y-2 text-sm">
        {items.map((item) => (
          <li key={item.text} className="border-l-2 border-primary pl-3">
            {item.text}
            <EvidenceRefs values={item.evidence_refs} onSelect={onEvidence} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function EvidenceRefs({
  values,
  onSelect,
}: {
  values: number[];
  onSelect: EvidenceHandler;
}) {
  const unique = [...new Set(values)];
  return unique.length ? (
    <span className="mt-2 flex flex-wrap gap-x-2 text-xs">
      {unique.map((id) => (
        <button
          key={id}
          type="button"
          className="rounded-sm font-mono text-link hover:text-[var(--link-deep)] hover:underline focus-visible:outline-none focus-visible:shadow-geist-focus"
          onClick={() => onSelect(id)}
        >
          #{id}
        </button>
      ))}
    </span>
  ) : null;
}

function SourceAssessmentList({
  items,
  onEvidence,
}: {
  items: SourceAssessmentView[];
  onEvidence: EvidenceHandler;
}) {
  const t = useTranslations("workbench");
  return (
    <section>
      <h3 className="font-semibold">{t("sourceAssessments")}</h3>
      <div className="dashboard-record-list mt-2">
        {items.map((item, index) => (
          <article
            key={`${item.repository_id}:${item.revision || index}`}
            className="dashboard-record"
          >
            <dl>
              <AssessmentRow
                label={t("assessmentRepository")}
                value={`#${item.repository_id}`}
              />
              {item.build_unit_id ? (
                <AssessmentRow
                  label={t("assessmentBuildUnit")}
                  value={`#${item.build_unit_id}`}
                />
              ) : null}
              {item.component_id ? (
                <AssessmentRow
                  label={t("assessmentComponent")}
                  value={`#${item.component_id}`}
                />
              ) : null}
              <AssessmentRow
                label={t("assessmentRevision")}
                value={item.revision || t("assessmentUnavailable")}
                mono
              />
              <AssessmentRow
                label={t("assessmentRevisionOrigin")}
                value={t(`assessmentRevisionOrigins.${item.revision_origin}`)}
              />
              <AssessmentRow
                label={t("assessmentAuthority")}
                value={t(
                  `assessmentAuthorityStatuses.${item.authority_status}`,
                )}
              />
              <AssessmentRow
                label={t("assessmentCompatibility")}
                value={t(
                  `assessmentCompatibilityStatuses.${item.compatibility_status}`,
                )}
              />
              {item.mismatch_reasons.length > 0 ? (
                <AssessmentRow
                  label={t("assessmentMismatches")}
                  value={t("assessmentMismatchCount", {
                    count: item.mismatch_reasons.length,
                  })}
                />
              ) : null}
            </dl>
            <EvidenceRefs values={item.evidence_refs} onSelect={onEvidence} />
          </article>
        ))}
      </div>
    </section>
  );
}

function ConfigurationAssessmentList({
  items,
  onEvidence,
}: {
  items: ConfigurationAssessmentView[];
  onEvidence: EvidenceHandler;
}) {
  const t = useTranslations("workbench");
  return (
    <section>
      <h3 className="font-semibold">{t("configurationAssessments")}</h3>
      <div className="dashboard-record-list mt-2">
        {items.map((item, index) => (
          <article key={`${item.scope}:${index}`} className="dashboard-record">
            <dl>
              <AssessmentRow label={t("assessmentScope")} value={item.scope} />
              <AssessmentRow
                label={t("assessmentDeclaredValue")}
                value={formatAssessmentValue(item.declared_value, t)}
              />
              <AssessmentRow
                label={t("assessmentRuntimeValue")}
                value={formatAssessmentValue(item.runtime_value, t)}
              />
              <AssessmentRow
                label={t("assessmentStatus")}
                value={t(
                  `assessmentEffectiveStatuses.${item.effective_status}`,
                )}
              />
            </dl>
            <EvidenceRefs values={item.evidence_refs} onSelect={onEvidence} />
          </article>
        ))}
      </div>
    </section>
  );
}

function AssessmentRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="dashboard-key-value">
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value}</dd>
    </div>
  );
}

function formatAssessmentValue(
  value: unknown,
  t: ReturnType<typeof useTranslations>,
) {
  if (value === null || value === undefined || value === "")
    return t("assessmentUnavailable");
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  )
    return String(value);
  if (Array.isArray(value))
    return t("assessmentValueItems", { count: value.length });
  if (typeof value === "object")
    return t("assessmentValueFields", { count: Object.keys(value).length });
  return t("assessmentUnavailable");
}

function CausalGraphPanel({
  graph,
  onEvidence,
}: {
  graph: CausalGraph;
  onEvidence: EvidenceHandler;
}) {
  const t = useTranslations("workbench");
  return (
    <section>
      <h3 className="font-semibold">{t("causalGraph")}</h3>
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(260px,.65fr)]">
        <div className="grid gap-2 sm:grid-cols-2">
          {graph.nodes.map((node) => (
            <article
              key={node.node_id}
              id={`causal-${node.node_id}`}
              className={`dashboard-record border-l-4 ${node.status === "confirmed" ? "border-primary" : node.status === "refuted" ? "border-destructive" : "border-warning"}`}
            >
              <div className="flex justify-between gap-2">
                <strong className="text-sm">
                  {t(`causalNodeTypes.${node.node_type}`)}
                </strong>
                <span className="text-xs text-muted-foreground">
                  {causalStatusLabel(node.status, t)}
                </span>
              </div>
              <p className="mt-2 text-sm">{node.statement}</p>
              <EvidenceRefs values={node.evidence_refs} onSelect={onEvidence} />
            </article>
          ))}
        </div>
        <div className="dashboard-record-list">
          {graph.edges.map((edge) => (
            <div key={edge.edge_id} className="dashboard-record text-sm">
              <strong>
                {edge.source_node_id} → {edge.target_node_id}
              </strong>
              <p className="mt-1 text-muted-foreground">
                {t(`causalRelations.${edge.relation}`)} · {edge.statement}
              </p>
              <EvidenceRefs values={edge.evidence_refs} onSelect={onEvidence} />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
