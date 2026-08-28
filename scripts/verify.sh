#!/usr/bin/env bash
# Verify the schema: spin a throwaway local Postgres, run Alembic, print summary.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PGDATA="$(mktemp -d)"
PORT=5433
DATABASE_NAME="lode_test_verify"
URL="postgresql+asyncpg://postgres@localhost:${PORT}/${DATABASE_NAME}"

cleanup() {
  pg_ctl -D "$PGDATA" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$PGDATA"
}
trap cleanup EXIT

echo "==> initializing local postgres at $PGDATA (port $PORT)"
initdb -D "$PGDATA" -U postgres --auth=trust >/dev/null
pg_ctl -D "$PGDATA" -o "-p $PORT -k $PGDATA" -l "$PGDATA/pg.log" start
sleep 2
createdb -h localhost -p "$PORT" -U postgres "$DATABASE_NAME"

export LODE_DATABASE_URL="$URL"
export LODE_TOOLING_ISOLATED_DATABASE=1
export LODE_MASTER_KEY="lode-isolated-test-master-key-0001"
export LODE_COMMAND_RUNNER_KEY="lode-isolated-runner-key-00000001"
export LODE_KAFKA_SECURITY_PROTOCOL=PLAINTEXT
export LODE_KAFKA_SASL_USERNAME=""
export LODE_KAFKA_SASL_PASSWORD=""
export LODE_KAFKA_SSL_CA_FILE=""
export PATH="$VENV/bin:$PATH"

echo "==> running migrations (alembic upgrade head)"
alembic upgrade head

echo "==> tables"
psql -h localhost -p "$PORT" -U postgres -d lode -c "\dt public.*"

echo "==> alerts column types / constraints"
psql -h localhost -p "$PORT" -U postgres -d lode -c "\d alerts"

echo "==> indexes on alerts"
psql -h localhost -p "$PORT" -U postgres -d lode -c "\di *alerts*"

if (( $# > 0 )); then
  echo "==> running verification command: $*"
  "$@"
fi

echo "==> OK"
