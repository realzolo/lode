# Lode V1 Contract Fixtures

This directory is the machine-readable boundary frozen by Phase 0. Runtime
implementations must consume these contracts directly or prove that their
generated schemas are structurally identical.

- `kafka/incident-alert.schema.json`: the only Kafka alert payload.
- `evidence/native-read-candidate.schema.json`: untrusted AI read candidate.
- `ai/investigation-decision.schema.json`: one adaptive decision wave.
- `ai/native-query.schema.json`: operation-bound provider query output.
- `ai/investigation-report.schema.json`: terminal report shape.
- `control-plane/entities.schema.json`: JavaScript-safe compact entity IDs,
  provider-kind/protocol model portfolios, discovery state, and context snapshot
  objects.
- `api/endpoints.json`: final HTTP surface.
- `database/tables.json`: the single V1 schema inventory.
- `database/invariants.json`: required immutability, archive, timestamp, and
  cross-table trigger coverage.

Fixtures are append-only only after V1 release. Before release, a contract
change replaces the fixture and all dependent implementation in the same
change; no aliases or compatibility schemas are permitted.

Public entity IDs are positive integers no greater than `2^52-1`; revision,
ordinal, and event-cursor integers are not entity IDs. Provider account requests
use `provider_kind`, `protocol_id`, `base_url`, `api_key`, and structured
`models` entries whose source is `discovered` or `manual`. API Keys and encrypted
secret fields are never response properties.
