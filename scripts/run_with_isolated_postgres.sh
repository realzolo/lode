#!/usr/bin/env bash
set -euo pipefail

if [[ "${LODE_TOOLING_ISOLATED_DATABASE:-}" == "1" ]]; then
  exec "$@"
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_PGDATA="$(mktemp -d "${TMPDIR:-/tmp}/lode-test-postgres.XXXXXX")"
TEST_DATABASE_NAME="lode_test_$$"
TEST_SOCKET_PORT=5432
UV_BIN="${UV:-uv}"

cleanup() {
  if [[ -n "${TEST_PGDATA:-}" && -d "$TEST_PGDATA" ]]; then
    pg_ctl -D "$TEST_PGDATA" -m immediate -w stop >/dev/null 2>&1 || true
    rm -rf -- "$TEST_PGDATA"
  fi
}
trap cleanup EXIT

initdb -D "$TEST_PGDATA" -U postgres --auth=trust --no-sync >/dev/null
pg_ctl \
  -D "$TEST_PGDATA" \
  -o "-h '' -k $TEST_PGDATA -p $TEST_SOCKET_PORT" \
  -l "$TEST_PGDATA/postgres.log" \
  -w start >/dev/null
createdb -h "$TEST_PGDATA" -p "$TEST_SOCKET_PORT" -U postgres "$TEST_DATABASE_NAME"

export LODE_DATABASE_URL="postgresql+asyncpg://postgres@/${TEST_DATABASE_NAME}?host=${TEST_PGDATA}"
export LODE_TOOLING_ISOLATED_DATABASE=1
export LODE_MASTER_KEY="lode-isolated-test-master-key-0001"
export LODE_COMMAND_RUNNER_KEY="lode-isolated-runner-key-00000001"
export LODE_KAFKA_SECURITY_PROTOCOL=PLAINTEXT
export LODE_KAFKA_SASL_USERNAME=""
export LODE_KAFKA_SASL_PASSWORD=""
export LODE_KAFKA_SSL_CA_FILE=""

cd "$PROJECT_ROOT"
"$UV_BIN" run alembic upgrade head >/dev/null
"$@"
