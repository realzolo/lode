#!/usr/bin/env bash
# Verify the schema: spin a throwaway local Postgres, run Alembic, print summary.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PGDATA="$(mktemp -d)"
PORT=5433
URL="postgresql+asyncpg://postgres@localhost:${PORT}/lode"

cleanup() {
  pg_ctl -D "$PGDATA" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$PGDATA"
}
trap cleanup EXIT

echo "==> initializing local postgres at $PGDATA (port $PORT)"
initdb -D "$PGDATA" -U postgres --auth=trust >/dev/null
pg_ctl -D "$PGDATA" -o "-p $PORT -k $PGDATA" -l "$PGDATA/pg.log" start
sleep 2
createdb -h localhost -p "$PORT" -U postgres lode

export LODE_DATABASE_URL="$URL"
export PATH="$VENV/bin:$PATH"

echo "==> running migrations (alembic upgrade head)"
alembic upgrade head

echo "==> tables"
psql -h localhost -p "$PORT" -U postgres -d lode -c "\dt public.*"

echo "==> alerts column types / constraints"
psql -h localhost -p "$PORT" -U postgres -d lode -c "\d alerts"

echo "==> indexes on alerts"
psql -h localhost -p "$PORT" -U postgres -d lode -c "\di *alerts*"

echo "==> OK"
