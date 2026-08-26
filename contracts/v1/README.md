# Lode V1 Contract Fixtures

This directory is the machine-readable boundary frozen by Phase 0. Runtime
implementations must consume these contracts directly or prove that their
generated schemas are structurally identical.

- `kafka/incident-alert.schema.json`: the only Kafka alert payload.
- `evidence/native-read-candidate.schema.json`: untrusted AI read candidate.
- `ai/investigation-decision.schema.json`: one adaptive decision wave.
- `ai/investigation-report.schema.json`: terminal report shape.
- `control-plane/entities.schema.json`: model portfolio and context snapshot objects.
- `api/endpoints.json`: final HTTP surface.
- `database/tables.json`: the single V1 schema inventory.
- `database/invariants.json`: required immutability, archive, timestamp, and
  cross-table trigger coverage.

Fixtures are append-only only after V1 release. Before release, a contract
change replaces the fixture and all dependent implementation in the same
change; no aliases or compatibility schemas are permitted.
