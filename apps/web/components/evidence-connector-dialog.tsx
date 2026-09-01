"use client";

import {
  cloneElement,
  isValidElement,
  useState,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Plus, Trash2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, apiErrorMessage, createConnector } from "@/lib/api";
import type { ConnectorCreateInput, LokiFilterInput } from "@/lib/types";

type ConnectorKind = ConnectorCreateInput["kind"];
type Authentication = "none" | "bearer_token" | "api_key" | "basic";
type DatabaseTlsMode = "verify_full" | "require" | "disabled";
type LokiOperator = "equals" | "not_equals" | "any_of" | "not_any_of";
type LokiCondition = {
  kind: "condition";
  label: string;
  operator: LokiOperator;
  value: string;
  values: string[];
};
type LokiGroup = {
  kind: "group";
  combinator: "all" | "any";
  items: Array<LokiCondition | LokiGroup>;
};
type FieldErrors = Record<string, string>;

const TYPED_PROVIDER_KINDS = new Set<ConnectorKind>([
  "prometheus",
  "tempo",
  "jaeger",
  "kubernetes",
  "github",
  "gitlab",
  "argocd",
]);
const ENDPOINT_KINDS = new Set<ConnectorKind>([
  "loki",
  "elasticsearch",
  "opensearch",
  "https",
  ...TYPED_PROVIDER_KINDS,
]);
const SEARCH_KINDS = new Set<ConnectorKind>(["elasticsearch", "opensearch"]);
const DATABASE_KINDS = new Set<ConnectorKind>([
  "postgresql",
  "mysql",
  "clickhouse",
]);
const MULTI_VALUE_OPERATORS = new Set<LokiOperator>(["any_of", "not_any_of"]);
const EXACT_INDEX = /^[a-z0-9][a-z0-9_.-]{0,254}$/;
const POSTGRES_SCHEMA = /^[A-Za-z_][A-Za-z0-9_$-]{0,62}$/;

const emptyCondition = (): LokiCondition => ({
  kind: "condition",
  label: "",
  operator: "equals",
  value: "",
  values: [],
});
const initialFilter = (): LokiGroup => ({
  kind: "group",
  combinator: "all",
  items: [emptyCondition()],
});

function normalizeValues(values: string[]): string[] {
  return [
    ...new Set(
      values
        .flatMap((value) => value.split(/[\n,，]/))
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ];
}

function conditionValues(condition: LokiCondition): string[] {
  return MULTI_VALUE_OPERATORS.has(condition.operator)
    ? normalizeValues([...condition.values, condition.value])
    : condition.value.trim()
      ? [condition.value.trim()]
      : [];
}

function toLokiFilterPayload(group: LokiGroup): LokiFilterInput {
  return {
    kind: "group",
    combinator: group.combinator,
    items: group.items.map((item) =>
      item.kind === "condition"
        ? {
            kind: "condition",
            label: item.label.trim(),
            operator: item.operator,
            values: conditionValues(item),
          }
        : toLokiFilterPayload(item),
    ),
  };
}

function lokiFilterIsComplete(item: LokiCondition | LokiGroup): boolean {
  if (item.kind === "condition")
    return Boolean(item.label.trim() && conditionValues(item).length);
  return item.items.length > 0 && item.items.every(lokiFilterIsComplete);
}

function positiveBranches(item: LokiCondition | LokiGroup): boolean[] {
  if (item.kind === "condition") {
    return [
      (["equals", "any_of"] as LokiOperator[]).includes(item.operator) &&
        conditionValues(item).length > 0,
    ];
  }
  if (item.combinator === "any") return item.items.flatMap(positiveBranches);
  return item.items.reduce<boolean[]>(
    (branches, child) => {
      const childBranches = positiveBranches(child);
      return branches.flatMap((branch) =>
        childBranches.map((childBranch) => branch || childBranch),
      );
    },
    [false],
  );
}

function isEndpointOrigin(
  value: string,
  allowedProtocols: Set<string>,
): boolean {
  try {
    const url = new URL(value);
    return (
      allowedProtocols.has(url.protocol) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      url.pathname === "/"
    );
  } catch {
    return false;
  }
}

function isSafePath(value: string): boolean {
  return /^\/[A-Za-z0-9._~/-]*$/.test(value);
}

export function EvidenceConnectorDialog({
  open,
  onOpenChange,
  workspaceId,
  kinds,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceId: string;
  kinds: Array<{ kind: string }>;
  onCreated: () => Promise<void>;
}) {
  const t = useTranslations("workspace");
  const tc = useTranslations("common");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ConnectorKind | "">("");
  const [endpoint, setEndpoint] = useState("");
  const [authentication, setAuthentication] = useState<Authentication>("none");
  const [credential, setCredential] = useState("");
  const [credentialUsername, setCredentialUsername] = useState("");
  const [verificationPath, setVerificationPath] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [safeReadPath, setSafeReadPath] = useState("");
  const [namespace, setNamespace] = useState("");
  const [owner, setOwner] = useState("");
  const [repository, setRepository] = useState("");
  const [projectId, setProjectId] = useState("");
  const [allowedIndices, setAllowedIndices] = useState<string[]>([]);
  const [allowedIndicesDraft, setAllowedIndicesDraft] = useState("");
  const [allowedSchemas, setAllowedSchemas] = useState<string[]>([]);
  const [allowedSchemasDraft, setAllowedSchemasDraft] = useState("");
  const [rootFilter, setRootFilter] = useState<LokiGroup>(initialFilter);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [database, setDatabase] = useState("");
  const [databaseUsername, setDatabaseUsername] = useState("");
  const [databasePassword, setDatabasePassword] = useState("");
  const [databaseTlsMode, setDatabaseTlsMode] =
    useState<DatabaseTlsMode>("verify_full");
  const [caCertificatePem, setCaCertificatePem] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [requestError, setRequestError] = useState("");

  const isEndpointConnector = kind !== "" && ENDPOINT_KINDS.has(kind);
  const isSearchConnector = kind !== "" && SEARCH_KINDS.has(kind);
  const isDatabaseConnector = kind !== "" && DATABASE_KINDS.has(kind);

  function resetForm() {
    setName("");
    setKind("");
    setEndpoint("");
    setAuthentication("none");
    setCredential("");
    setCredentialUsername("");
    setVerificationPath("");
    setTenantId("");
    setSafeReadPath("");
    setNamespace("");
    setOwner("");
    setRepository("");
    setProjectId("");
    setAllowedIndices([]);
    setAllowedIndicesDraft("");
    setAllowedSchemas([]);
    setAllowedSchemasDraft("");
    setRootFilter(initialFilter());
    setHost("");
    setPort("");
    setDatabase("");
    setDatabaseUsername("");
    setDatabasePassword("");
    setDatabaseTlsMode("verify_full");
    setCaCertificatePem("");
    setErrors({});
    setRequestError("");
  }

  function changeKind(value: string) {
    const nextKind = value as ConnectorKind;
    setKind(nextKind);
    setEndpoint("");
    setAuthentication(nextKind === "loki" ? "none" : "bearer_token");
    setCredential("");
    setCredentialUsername("");
    setVerificationPath("");
    setTenantId("");
    setSafeReadPath("");
    setNamespace("");
    setOwner("");
    setRepository("");
    setProjectId("");
    setAllowedIndices([]);
    setAllowedIndicesDraft("");
    setAllowedSchemas([]);
    setAllowedSchemasDraft("");
    setRootFilter(initialFilter());
    setHost("");
    setPort("");
    setDatabase("");
    setDatabaseUsername("");
    setDatabasePassword("");
    setDatabaseTlsMode("verify_full");
    setCaCertificatePem("");
    setErrors({});
    setRequestError("");
  }

  function changeAuthentication(value: string) {
    setAuthentication(value as Authentication);
    setCredential("");
    setCredentialUsername("");
    setErrors((current) => {
      const {
        credential: ignoredCredential,
        credentialUsername: ignoredUsername,
        ...remaining
      } = current;
      void ignoredCredential;
      void ignoredUsername;
      return remaining;
    });
  }

  function clearFieldError(field: string) {
    setErrors((current) => {
      if (!current[field]) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  function changeDatabaseTlsMode(value: string) {
    setDatabaseTlsMode(value as DatabaseTlsMode);
    setCaCertificatePem("");
    clearFieldError("caCertificatePem");
  }

  function validate(): FieldErrors {
    const next: FieldErrors = {};
    const required = t("connectorFieldRequired");
    if (!name.trim()) next.name = required;
    if (!kind) next.kind = required;
    if (!kind) return next;

    if (ENDPOINT_KINDS.has(kind)) {
      if (!endpoint.trim()) next.endpoint = required;
      else if (!isEndpointOrigin(endpoint.trim(), new Set(["http:", "https:"])))
        next.endpoint = t("connectorHttpOriginInvalid");
      if (authentication === "basic" && !credentialUsername.trim())
        next.credentialUsername = required;
      if (authentication !== "none" && !credential) next.credential = required;
    }

    if (kind === "loki") {
      if (!lokiFilterIsComplete(rootFilter))
        next.rootFilter = t("connectorLokiConditionsIncomplete");
      else if (!positiveBranches(rootFilter).every(Boolean))
        next.rootFilter = t("connectorLokiPositiveMatcherRequired");
    }

    if (SEARCH_KINDS.has(kind)) {
      const indices = normalizeValues([...allowedIndices, allowedIndicesDraft]);
      if (!indices.length) next.allowedIndices = required;
      else if (
        indices.some(
          (index) =>
            !EXACT_INDEX.test(index) ||
            index.includes("..") ||
            index.startsWith(".") ||
            ["all", "_all"].includes(index),
        )
      ) {
        next.allowedIndices = t("connectorExactIndexInvalid");
      }
    }

    if (kind === "https") {
      if (verificationPath.trim() && !isSafePath(verificationPath.trim()))
        next.verificationPath = t("connectorPathInvalid");
      if (!safeReadPath.trim()) next.safeReadPath = required;
      else if (!isSafePath(safeReadPath.trim()))
        next.safeReadPath = t("connectorPathInvalid");
    }
    if (
      kind === "kubernetes" &&
      !/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(namespace)
    )
      next.namespace = required;
    if (kind === "github") {
      if (!/^[A-Za-z0-9_.-]+$/.test(owner)) next.owner = required;
      if (!/^[A-Za-z0-9_.-]+$/.test(repository)) next.repository = required;
    }
    if (
      kind === "gitlab" &&
      (!/^\d+$/.test(projectId) || Number(projectId) < 1)
    )
      next.projectId = required;

    if (DATABASE_KINDS.has(kind)) {
      if (!host.trim()) next.host = required;
      if (
        port &&
        (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65_535)
      )
        next.port = t("connectorPortInvalid");
      if (!database.trim()) next.database = required;
      if (!databaseUsername.trim()) next.databaseUsername = required;
      if (!databasePassword) next.databasePassword = required;
      if (databaseTlsMode === "verify_full" && caCertificatePem.length > 64_000)
        next.caCertificatePem = t("connectorCaTooLarge");
      else if (
        databaseTlsMode === "verify_full" &&
        caCertificatePem.toUpperCase().includes("PRIVATE KEY-----")
      ) {
        next.caCertificatePem = t("connectorCaPrivateKeyInvalid");
      }
    }

    if (kind === "postgresql") {
      const schemas = normalizeValues([...allowedSchemas, allowedSchemasDraft]);
      if (!schemas.length) next.allowedSchemas = required;
      else if (
        schemas.length > 32 ||
        schemas.some(
          (schema) =>
            !POSTGRES_SCHEMA.test(schema) ||
            ["information_schema", "pg_catalog"].includes(
              schema.toLowerCase(),
            ) ||
            schema.toLowerCase().startsWith("pg_"),
        )
      ) {
        next.allowedSchemas = t("connectorSchemaInvalid");
      }
    }
    return next;
  }

  function focusFirstError(nextErrors: FieldErrors) {
    const first = Object.keys(nextErrors)[0];
    if (!first) return;
    requestAnimationFrame(() => {
      const field = document.querySelector<HTMLElement>(
        `[data-connector-field="${first}"]`,
      );
      field?.querySelector<HTMLElement>("input,button")?.focus();
    });
  }

  function buildPayload(): ConnectorCreateInput {
    const common = { name: name.trim() };
    if (kind === "loki") {
      return {
        ...common,
        kind,
        endpoint: endpoint.trim(),
        authentication: authentication as "none" | "bearer_token",
        ...(authentication === "bearer_token" ? { credential } : {}),
        ...(tenantId.trim() ? { tenant_id: tenantId.trim() } : {}),
        root_filter: toLokiFilterPayload(rootFilter),
      };
    }
    if (kind === "elasticsearch" || kind === "opensearch") {
      return {
        ...common,
        kind,
        endpoint: endpoint.trim(),
        authentication: authentication as Exclude<Authentication, "none">,
        credential,
        ...(authentication === "basic"
          ? { credential_username: credentialUsername.trim() }
          : {}),
        allowed_indices: normalizeValues([
          ...allowedIndices,
          allowedIndicesDraft,
        ]),
      };
    }
    if (kind === "postgresql") {
      return {
        ...common,
        kind,
        host: host.trim(),
        ...(port ? { port: Number(port) } : {}),
        database: database.trim(),
        database_username: databaseUsername.trim(),
        database_password: databasePassword,
        tls_mode: databaseTlsMode as "verify_full" | "require",
        ...(caCertificatePem.trim()
          ? { ca_certificate_pem: caCertificatePem.trim() }
          : {}),
        allowed_schemas: normalizeValues([
          ...allowedSchemas,
          allowedSchemasDraft,
        ]),
      };
    }
    if (kind === "mysql") {
      return {
        ...common,
        kind,
        host: host.trim(),
        ...(port ? { port: Number(port) } : {}),
        database: database.trim(),
        database_username: databaseUsername.trim(),
        database_password: databasePassword,
        tls_mode: databaseTlsMode as "verify_full" | "require",
        ...(caCertificatePem.trim()
          ? { ca_certificate_pem: caCertificatePem.trim() }
          : {}),
      };
    }
    if (kind === "clickhouse") {
      return {
        ...common,
        kind,
        host: host.trim(),
        ...(port ? { port: Number(port) } : {}),
        database: database.trim(),
        database_username: databaseUsername.trim(),
        database_password: databasePassword,
        tls_mode: databaseTlsMode,
        ...(caCertificatePem.trim()
          ? { ca_certificate_pem: caCertificatePem.trim() }
          : {}),
      };
    }
    if (kind && TYPED_PROVIDER_KINDS.has(kind)) {
      return {
        ...common,
        kind,
        endpoint: endpoint.trim(),
        authentication: authentication as Exclude<Authentication, "none">,
        credential,
        ...(authentication === "basic"
          ? { credential_username: credentialUsername.trim() }
          : {}),
        ...(kind === "kubernetes" ? { namespace } : {}),
        ...(kind === "github" ? { owner, repository } : {}),
        ...(kind === "gitlab" ? { project_id: Number(projectId) } : {}),
      } as ConnectorCreateInput;
    }
    return {
      ...common,
      kind: "https",
      endpoint: endpoint.trim(),
      authentication: authentication as Exclude<Authentication, "none">,
      credential,
      ...(authentication === "basic"
        ? { credential_username: credentialUsername.trim() }
        : {}),
      ...(verificationPath.trim()
        ? { verification_path: verificationPath.trim() }
        : {}),
      safe_read_path: safeReadPath.trim(),
    };
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setRequestError("");
    const nextErrors = validate();
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) {
      focusFirstError(nextErrors);
      return;
    }

    setSubmitting(true);
    try {
      await createConnector(workspaceId, buildPayload());
      await onCreated();
      resetForm();
      onOpenChange(false);
    } catch (cause) {
      const message = apiErrorMessage(cause, tc("requestFailed"));
      const sqlstate =
        cause instanceof ApiError ? cause.details?.sqlstate : null;
      setRequestError(
        typeof sqlstate === "string" && /^[0-9A-Z]{5}$/.test(sqlstate)
          ? `${message} ${t("connectorPostgresSqlstate", { sqlstate })}`
          : message,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => !submitting && onOpenChange(value)}
    >
      <DialogContent variant="drawer" className="max-w-2xl overflow-hidden p-0">
        <DialogHeader className="border-b px-6 py-5">
          <DialogTitle>{t("addEvidenceConnector")}</DialogTitle>
        </DialogHeader>
        <form onSubmit={(event) => void submit(event)}>
          <div className="h-[calc(100dvh-145px)] space-y-7 overflow-y-auto px-6 py-5">
            {requestError ? (
              <p role="alert" className="dashboard-feedback">
                {requestError}
              </p>
            ) : null}

            <ConnectorSection title={t("connectorBasicInformation")}>
              <ConnectorField
                name="name"
                label={t("name")}
                error={errors.name}
                required
              >
                <Input
                  placeholder={t("connectorNamePlaceholder")}
                  value={name}
                  aria-invalid={Boolean(errors.name)}
                  onChange={(event) => {
                    setName(event.target.value);
                    clearFieldError("name");
                  }}
                />
              </ConnectorField>
              <ConnectorField
                name="kind"
                label={t("connectorKind")}
                error={errors.kind}
                required
              >
                <Select
                  value={kind}
                  aria-invalid={Boolean(errors.kind)}
                  placeholder={t("connectorKindPlaceholder")}
                  onChange={(event) => changeKind(event.target.value)}
                >
                  {kinds.map((item) => (
                    <option key={item.kind} value={item.kind}>
                      {t(`connectorKinds.${item.kind}`)}
                    </option>
                  ))}
                </Select>
              </ConnectorField>
            </ConnectorSection>

            {kind ? (
              <ConnectorSection title={t("connectorConnectionInformation")}>
                {isEndpointConnector ? (
                  <ConnectorField
                    name="endpoint"
                    label={kind === "https" ? t("httpOrigin") : t("endpoint")}
                    error={errors.endpoint}
                    required
                  >
                    <Input
                      placeholder={
                        kind === "loki"
                          ? "http://loki.example.com:3100"
                          : kind === "https"
                            ? "http://api.example.com"
                            : "http://service.example.com"
                      }
                      value={endpoint}
                      aria-invalid={Boolean(errors.endpoint)}
                      onChange={(event) => {
                        setEndpoint(event.target.value);
                        clearFieldError("endpoint");
                      }}
                    />
                  </ConnectorField>
                ) : null}

                {kind === "loki" ? (
                  <ConnectorField name="tenantId" label={t("tenantId")}>
                    <Input
                      placeholder={t("tenantIdPlaceholder")}
                      value={tenantId}
                      onChange={(event) => setTenantId(event.target.value)}
                    />
                  </ConnectorField>
                ) : null}

                {kind === "kubernetes" ? (
                  <ConnectorField
                    name="namespace"
                    label={t("connectorNamespace")}
                    error={errors.namespace}
                    required
                  >
                    <Input
                      value={namespace}
                      aria-invalid={Boolean(errors.namespace)}
                      onChange={(event) => {
                        setNamespace(event.target.value);
                        clearFieldError("namespace");
                      }}
                    />
                  </ConnectorField>
                ) : null}
                {kind === "github" ? (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <ConnectorField
                      name="owner"
                      label={t("connectorOwner")}
                      error={errors.owner}
                      required
                    >
                      <Input
                        value={owner}
                        aria-invalid={Boolean(errors.owner)}
                        onChange={(event) => {
                          setOwner(event.target.value);
                          clearFieldError("owner");
                        }}
                      />
                    </ConnectorField>
                    <ConnectorField
                      name="repository"
                      label={t("connectorRepository")}
                      error={errors.repository}
                      required
                    >
                      <Input
                        value={repository}
                        aria-invalid={Boolean(errors.repository)}
                        onChange={(event) => {
                          setRepository(event.target.value);
                          clearFieldError("repository");
                        }}
                      />
                    </ConnectorField>
                  </div>
                ) : null}
                {kind === "gitlab" ? (
                  <ConnectorField
                    name="projectId"
                    label={t("connectorProjectId")}
                    error={errors.projectId}
                    required
                  >
                    <Input
                      inputMode="numeric"
                      value={projectId}
                      aria-invalid={Boolean(errors.projectId)}
                      onChange={(event) => {
                        setProjectId(event.target.value);
                        clearFieldError("projectId");
                      }}
                    />
                  </ConnectorField>
                ) : null}

                {isEndpointConnector ? (
                  <>
                    <ConnectorField
                      name="authentication"
                      label={t("authentication")}
                      required
                    >
                      <Select
                        value={authentication}
                        onChange={(event) =>
                          changeAuthentication(event.target.value)
                        }
                      >
                        {kind === "loki" ? (
                          <option value="none">{t("none")}</option>
                        ) : null}
                        <option value="bearer_token">{t("bearerToken")}</option>
                        {kind !== "loki" ? (
                          <option value="api_key">{t("apiKey")}</option>
                        ) : null}
                        {kind !== "loki" ? (
                          <option value="basic">{t("basicAuth")}</option>
                        ) : null}
                      </Select>
                    </ConnectorField>
                    {authentication === "basic" ? (
                      <ConnectorField
                        name="credentialUsername"
                        label={t("username")}
                        error={errors.credentialUsername}
                        required
                      >
                        <Input
                          value={credentialUsername}
                          aria-invalid={Boolean(errors.credentialUsername)}
                          onChange={(event) => {
                            setCredentialUsername(event.target.value);
                            clearFieldError("credentialUsername");
                          }}
                        />
                      </ConnectorField>
                    ) : null}
                    {authentication !== "none" ? (
                      <ConnectorField
                        name="credential"
                        label={
                          authentication === "api_key"
                            ? t("apiKey")
                            : authentication === "basic"
                              ? t("password")
                              : t("accessToken")
                        }
                        error={errors.credential}
                        required
                      >
                        <Input
                          type="password"
                          value={credential}
                          aria-invalid={Boolean(errors.credential)}
                          onChange={(event) => {
                            setCredential(event.target.value);
                            clearFieldError("credential");
                          }}
                        />
                      </ConnectorField>
                    ) : null}
                  </>
                ) : null}

                {isDatabaseConnector ? (
                  <>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <ConnectorField
                        name="host"
                        label={t("databaseHost")}
                        error={errors.host}
                        required
                      >
                        <Input
                          value={host}
                          aria-invalid={Boolean(errors.host)}
                          onChange={(event) => {
                            setHost(event.target.value);
                            clearFieldError("host");
                          }}
                        />
                      </ConnectorField>
                      <ConnectorField
                        name="port"
                        label={t("databasePort")}
                        error={errors.port}
                      >
                        <Input
                          inputMode="numeric"
                          placeholder={
                            kind === "postgresql"
                              ? t("postgresPortPlaceholder")
                              : kind === "mysql"
                                ? t("mysqlPortPlaceholder")
                                : databaseTlsMode === "disabled"
                                  ? t("clickhouseHttpPortPlaceholder")
                                  : t("clickhousePortPlaceholder")
                          }
                          value={port}
                          aria-invalid={Boolean(errors.port)}
                          onChange={(event) => {
                            setPort(event.target.value);
                            clearFieldError("port");
                          }}
                        />
                      </ConnectorField>
                      <ConnectorField
                        name="database"
                        label={t("databaseName")}
                        error={errors.database}
                        required
                      >
                        <Input
                          value={database}
                          aria-invalid={Boolean(errors.database)}
                          onChange={(event) => {
                            setDatabase(event.target.value);
                            clearFieldError("database");
                          }}
                        />
                      </ConnectorField>
                      <ConnectorField
                        name="databaseUsername"
                        label={t("username")}
                        error={errors.databaseUsername}
                        required
                      >
                        <Input
                          value={databaseUsername}
                          aria-invalid={Boolean(errors.databaseUsername)}
                          onChange={(event) => {
                            setDatabaseUsername(event.target.value);
                            clearFieldError("databaseUsername");
                          }}
                        />
                      </ConnectorField>
                    </div>
                    <ConnectorField
                      name="databasePassword"
                      label={t("password")}
                      error={errors.databasePassword}
                      required
                    >
                      <Input
                        type="password"
                        value={databasePassword}
                        aria-invalid={Boolean(errors.databasePassword)}
                        onChange={(event) => {
                          setDatabasePassword(event.target.value);
                          clearFieldError("databasePassword");
                        }}
                      />
                    </ConnectorField>
                    <ConnectorField
                      name="databaseTlsMode"
                      label={t("databaseTlsMode")}
                      required
                    >
                      <Select
                        value={databaseTlsMode}
                        onChange={(event) =>
                          changeDatabaseTlsMode(event.target.value)
                        }
                      >
                        <option value="verify_full">
                          {t("databaseTlsVerifyFull")}
                        </option>
                        <option value="require">
                          {t("databaseTlsRequire")}
                        </option>
                        {kind === "clickhouse" ? (
                          <option value="disabled">
                            {t("databaseTlsDisabled")}
                          </option>
                        ) : null}
                      </Select>
                    </ConnectorField>
                    {databaseTlsMode === "verify_full" ? (
                      <ConnectorField
                        name="caCertificatePem"
                        label={t("databaseCaCertificate")}
                        error={errors.caCertificatePem}
                      >
                        <Textarea
                          className="min-h-28 font-mono text-xs"
                          placeholder={t("databaseCaCertificatePlaceholder")}
                          value={caCertificatePem}
                          aria-invalid={Boolean(errors.caCertificatePem)}
                          spellCheck={false}
                          onChange={(event) => {
                            setCaCertificatePem(event.target.value);
                            clearFieldError("caCertificatePem");
                          }}
                        />
                      </ConnectorField>
                    ) : null}
                  </>
                ) : null}
              </ConnectorSection>
            ) : null}

            {kind ? (
              <ConnectorSection title={t("connectorReadScope")}>
                {kind === "loki" ? (
                  <ConnectorField
                    name="rootFilter"
                    label={t("lokiRootFilter")}
                    error={errors.rootFilter}
                    required
                  >
                    <LokiGroupEditor
                      group={rootFilter}
                      depth={1}
                      onChange={(value) => {
                        setRootFilter(value);
                        clearFieldError("rootFilter");
                      }}
                    />
                  </ConnectorField>
                ) : null}
                {isSearchConnector ? (
                  <ConnectorField
                    name="allowedIndices"
                    label={t("allowedIndices")}
                    error={errors.allowedIndices}
                    required
                  >
                    <StringListInput
                      values={allowedIndices}
                      draft={allowedIndicesDraft}
                      placeholder={t("allowedIndicesPlaceholder")}
                      invalid={Boolean(errors.allowedIndices)}
                      removeLabel={(value) =>
                        t("removeConnectorValue", { value })
                      }
                      onChange={(value) => {
                        setAllowedIndices(value);
                        clearFieldError("allowedIndices");
                      }}
                      onDraftChange={(value) => {
                        setAllowedIndicesDraft(value);
                        clearFieldError("allowedIndices");
                      }}
                      onCommit={(value) => {
                        setAllowedIndices(value);
                        setAllowedIndicesDraft("");
                        clearFieldError("allowedIndices");
                      }}
                    />
                  </ConnectorField>
                ) : null}
                {kind === "postgresql" ? (
                  <>
                    <p className="text-sm text-muted-foreground">
                      {t("databaseAutoDiscoveryDescription")}
                    </p>
                    <ConnectorField
                      name="allowedSchemas"
                      label={t("allowedSchemas")}
                      error={errors.allowedSchemas}
                      required
                    >
                      <StringListInput
                        values={allowedSchemas}
                        draft={allowedSchemasDraft}
                        placeholder={t("allowedSchemasPlaceholder")}
                        invalid={Boolean(errors.allowedSchemas)}
                        removeLabel={(value) =>
                          t("removeConnectorValue", { value })
                        }
                        onChange={(value) => {
                          setAllowedSchemas(value);
                          clearFieldError("allowedSchemas");
                        }}
                        onDraftChange={(value) => {
                          setAllowedSchemasDraft(value);
                          clearFieldError("allowedSchemas");
                        }}
                        onCommit={(value) => {
                          setAllowedSchemas(value);
                          setAllowedSchemasDraft("");
                          clearFieldError("allowedSchemas");
                        }}
                      />
                    </ConnectorField>
                  </>
                ) : null}
                {kind === "mysql" ? (
                  <p className="text-sm text-muted-foreground">
                    {t("databaseAutoDiscoveryDescription")}
                  </p>
                ) : null}
                {kind === "clickhouse" ? (
                  <p className="text-sm text-muted-foreground">
                    {t("clickhouseAutoDiscoveryDescription")}
                  </p>
                ) : null}
                {kind === "https" ? (
                  <>
                    <ConnectorField
                      name="verificationPath"
                      label={t("verificationPath")}
                      error={errors.verificationPath}
                    >
                      <Input
                        placeholder={t("verificationPathPlaceholder")}
                        value={verificationPath}
                        aria-invalid={Boolean(errors.verificationPath)}
                        onChange={(event) => {
                          setVerificationPath(event.target.value);
                          clearFieldError("verificationPath");
                        }}
                      />
                    </ConnectorField>
                    <ConnectorField
                      name="safeReadPath"
                      label={t("safeReadPath")}
                      error={errors.safeReadPath}
                      required
                    >
                      <Input
                        placeholder="/v1/events"
                        value={safeReadPath}
                        aria-invalid={Boolean(errors.safeReadPath)}
                        onChange={(event) => {
                          setSafeReadPath(event.target.value);
                          clearFieldError("safeReadPath");
                        }}
                      />
                    </ConnectorField>
                  </>
                ) : null}
              </ConnectorSection>
            ) : null}
          </div>
          <DialogFooter className="border-t px-6 py-4">
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={() => onOpenChange(false)}
            >
              {tc("cancel")}
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={submitting}
              loadingText={t("creatingAndVerifyingConnector")}
            >
              {t("createAndVerifyConnector")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ConnectorSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4" aria-label={title}>
      <h3 className="border-b pb-2 text-sm font-medium text-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

function ConnectorField({
  name,
  label,
  required = false,
  error,
  children,
}: {
  name: string;
  label: string;
  required?: boolean;
  error?: string;
  children: ReactNode;
}) {
  const controlId = `connector-${name}`;
  const labelId = `${controlId}-label`;
  const errorId = `${controlId}-error`;
  const control = isValidElement<{
    id?: string;
    "aria-labelledby"?: string;
    "aria-describedby"?: string;
    "aria-required"?: boolean;
  }>(children)
    ? cloneElement(children, {
        id: controlId,
        "aria-labelledby": labelId,
        "aria-describedby": error ? errorId : undefined,
        "aria-required": required || undefined,
      })
    : children;
  return (
    <div className="field" data-connector-field={name}>
      <span id={labelId} className="field-label">
        {label}
        {required ? (
          <span className="ml-1 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </span>
      {control}
      {error ? (
        <span id={errorId} role="alert" className="text-xs text-destructive">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function StringListInput({
  values,
  draft,
  placeholder,
  invalid,
  removeLabel,
  onChange,
  onDraftChange,
  onCommit,
  id,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
  "aria-required": ariaRequired,
}: {
  values: string[];
  draft: string;
  placeholder: string;
  invalid: boolean;
  removeLabel: (value: string) => string;
  onChange: (values: string[]) => void;
  onDraftChange: (value: string) => void;
  onCommit: (values: string[]) => void;
  id?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  "aria-required"?: boolean;
}) {
  function commit(value = draft) {
    const next = normalizeValues([...values, value]);
    onCommit(next);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === "," || event.key === "，") {
      event.preventDefault();
      commit(event.currentTarget.value);
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLInputElement>) {
    const pasted = event.clipboardData.getData("text");
    if (!/[\n,，]/.test(pasted)) return;
    event.preventDefault();
    commit(`${event.currentTarget.value}${pasted}`);
  }

  return (
    <div
      className={`dashboard-token-input flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border bg-background px-2 py-1 ${invalid ? "border-destructive" : "border-input"}`}
    >
      {values.map((value) => (
        <span
          key={value}
          className="inline-flex h-7 max-w-full items-center gap-1 rounded-sm bg-muted px-2 text-xs"
        >
          <span className="truncate">{value}</span>
          <Tooltip content={removeLabel(value)}>
            <button
              type="button"
              className="-mr-1 inline-flex size-5 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:shadow-geist-focus"
              aria-label={removeLabel(value)}
              onClick={() => onChange(values.filter((item) => item !== value))}
            >
              <X size={13} />
            </button>
          </Tooltip>
        </span>
      ))}
      <Input
        id={id}
        className="dashboard-token-input-field h-7 min-w-40 flex-1 border-0 px-1 py-0 shadow-none focus-visible:shadow-none"
        value={draft}
        placeholder={values.length ? "" : placeholder}
        aria-invalid={invalid}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        aria-required={ariaRequired}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        onBlur={(event) =>
          event.currentTarget.value.trim() && commit(event.currentTarget.value)
        }
      />
    </div>
  );
}

function LokiGroupEditor({
  group,
  depth,
  onChange,
  removable = false,
  onRemove,
  id,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
  "aria-required": ariaRequired,
}: {
  group: LokiGroup;
  depth: number;
  onChange: (group: LokiGroup) => void;
  removable?: boolean;
  onRemove?: () => void;
  id?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  "aria-required"?: boolean;
}) {
  const t = useTranslations("workspace");

  function update(index: number, item: LokiCondition | LokiGroup) {
    const items = [...group.items];
    items[index] = item;
    onChange({ ...group, items });
  }

  function remove(index: number) {
    onChange({
      ...group,
      items: group.items.filter((_, itemIndex) => itemIndex !== index),
    });
  }

  function changeOperator(
    condition: LokiCondition,
    operator: LokiOperator,
  ): LokiCondition {
    if (MULTI_VALUE_OPERATORS.has(operator)) {
      return {
        ...condition,
        operator,
        values: normalizeValues([...condition.values, condition.value]),
        value: "",
      };
    }
    return {
      ...condition,
      operator,
      value: condition.value || condition.values[0] || "",
    };
  }

  return (
    <div
      id={id}
      className="space-y-2 border p-3"
      role="group"
      aria-labelledby={ariaLabelledBy}
      aria-describedby={ariaDescribedBy}
      aria-required={ariaRequired}
    >
      <div className="flex items-center justify-between gap-2">
        <Select
          aria-label={t("lokiRootFilter")}
          className="w-32"
          value={group.combinator}
          onChange={(event) =>
            onChange({
              ...group,
              combinator: event.target.value as "all" | "any",
            })
          }
        >
          <option value="all">{t("matchAll")}</option>
          <option value="any">{t("matchAny")}</option>
        </Select>
        {removable ? (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            title={t("removeGroup")}
            aria-label={t("removeGroup")}
            onClick={onRemove}
          >
            <Trash2 size={15} />
          </Button>
        ) : null}
      </div>
      {group.items.map((item, index) =>
        item.kind === "condition" ? (
          <div
            key={index}
            className="grid items-start gap-2 sm:grid-cols-[minmax(0,1fr)_140px_minmax(0,1fr)_32px]"
          >
            <Input
              aria-label={t("labelName")}
              placeholder={t("labelName")}
              value={item.label}
              onChange={(event) =>
                update(index, { ...item, label: event.target.value })
              }
            />
            <Select
              aria-label={t("lokiOperator")}
              value={item.operator}
              onChange={(event) =>
                update(
                  index,
                  changeOperator(item, event.target.value as LokiOperator),
                )
              }
            >
              <option value="equals">{t("equals")}</option>
              <option value="not_equals">{t("notEquals")}</option>
              <option value="any_of">{t("anyOf")}</option>
              <option value="not_any_of">{t("notAnyOf")}</option>
            </Select>
            {MULTI_VALUE_OPERATORS.has(item.operator) ? (
              <StringListInput
                values={item.values}
                draft={item.value}
                placeholder={t("labelValues")}
                invalid={false}
                removeLabel={(value) => t("removeConnectorValue", { value })}
                onChange={(values) => update(index, { ...item, values })}
                onDraftChange={(value) => update(index, { ...item, value })}
                onCommit={(values) =>
                  update(index, { ...item, values, value: "" })
                }
              />
            ) : (
              <Input
                aria-label={t("labelValue")}
                placeholder={t("labelValue")}
                value={item.value}
                onChange={(event) =>
                  update(index, { ...item, value: event.target.value })
                }
              />
            )}
            <Button
              type="button"
              size="icon"
              variant="ghost"
              title={t("removeCondition")}
              aria-label={t("removeCondition")}
              disabled={group.items.length === 1}
              onClick={() => remove(index)}
            >
              <Trash2 size={15} />
            </Button>
          </div>
        ) : (
          <LokiGroupEditor
            key={index}
            group={item}
            depth={depth + 1}
            removable
            onRemove={() => remove(index)}
            onChange={(value) => update(index, value)}
          />
        ),
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            onChange({ ...group, items: [...group.items, emptyCondition()] })
          }
        >
          <Plus size={14} />
          {t("addCondition")}
        </Button>
        {depth < 3 ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() =>
              onChange({ ...group, items: [...group.items, initialFilter()] })
            }
          >
            <Plus size={14} />
            {t("addGroup")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
