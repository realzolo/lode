# Incident Trace

AI-powered production incident root-cause analysis platform. Business services
publish a simplified error to a per-application Kafka topic; the platform consumes
it, recomputes a stable dedupe key, and (in later phases) runs an agentic analysis
that correlates code, read-only databases, deployment context, and shared memory.

This repository implements **Phase 1**: the production-grade data layer, the
Kafka ingestion contract, the agentic analysis engine, the authenticated REST API,
and the Next.js frontend that visualizes the workflow and supports human-in-the-loop
(hints / re-analysis).

## Stack

- Python 3.12+ (async), FastAPI, SQLAlchemy 2.0 (async), Alembic
- PostgreSQL 16+ (jsonb, timestamptz, GENERATED ALWAYS AS IDENTITY, partial indexes)
- aiokafka consumer
- Analysis engine: controlled read-only tools + LLM client with a deterministic
  heuristic fallback (runs fully offline when no model key is configured)
- Auth: PBKDF2 password hashing + HMAC-signed tokens (stdlib only, no extra deps)
- Frontend: Next.js 14 (App Router, TypeScript strict) + Geist + next-intl
- Tests: pytest + pytest-asyncio + httpx
- Containerized via Docker (backend + frontend) and docker-compose

## Repository layout

```
incident-trace/
├── alembic/                 # migration tool (async env + versions)
├── alembic.ini
├── Dockerfile               # backend API image (python:3.12-slim)
├── docker-compose.yml       # postgres + kafka + api + web
├── src/incident_trace/
│   ├── config.py            # settings (IT_* env vars)
│   ├── security.py          # password hashing + signed tokens (stdlib only)
│   ├── db/                  # Base, async engine/session, ORM models
│   │   └── models/          # tables, one module per domain
│   ├── engine/              # agentic runner, LLM client, read-only tools
│   ├── api/                 # FastAPI app (auto-migrates + auth + error envelope)
│   │   ├── deps.py          # require_user bearer-token dependency
│   │   └── routes/          # analyses, applications, memories, alerts, auth, settings
│   ├── migrations.py        # run `alembic upgrade head` from the server
│   └── consumer/            # Kafka consumer: v1.1 validation + dedupe key
├── apps/web/                # Next.js frontend (Dockerfile + standalone output)
├── scripts/                 # seed.py, set_admin_password.py
└── tests/                   # security, api-auth, engine, dedupe, schema tests
```

## Database naming conventions (PostgreSQL best practice)

- All identifiers **lowercase snake_case** (never quoted, never CamelCase).
- **Plural** table names (e.g. `applications`, `alerts`, `analyses`).
- Primary keys: `bigint GENERATED ALWAYS AS IDENTITY`.
- Timestamps: `timestamptz` with `DEFAULT now()`; an `updated_at` trigger keeps
  them current on every `UPDATE`.
- Semi-structured data: `jsonb` (not `json`); alert `fields` carry a GIN index.
- Booleans: `is_*` prefixed (`is_default`, `is_valid`, `readonly`).
- Integrity: explicit `CHECK` constraints on every enum-like column; FK columns
  named `<table>_id` with explicit `ON DELETE` rules.
- "Exactly one default model" enforced by **partial unique indexes**
  (`WHERE scope = 'global' AND is_default`, `WHERE scope = 'application' AND is_default`).

## Migrations (Alembic) — auto-executed

The schema is applied by Alembic. The server runs `alembic upgrade head`
automatically on startup (see `incident_trace.api.main.lifespan`), so a fresh
deploy is always schema-current before serving traffic.

```bash
make install          # pip install -e ".[dev]"
make dev-up           # docker compose up -d (postgres + kafka)
make migrate          # alembic upgrade head
make serve            # uvicorn (also migrates on boot)
make consume          # kafka consumer
make verify           # throwaway local Postgres + migrate + schema dump
```

Or manually:

```bash
alembic upgrade head
python -m incident_trace.consumer.main
```

## Authentication

All business endpoints require a bearer token. Log in to obtain one:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@incident-trace.local","password":"incident-trace"}'
```

- Passwords are hashed with PBKDF2-HMAC-SHA256 (per-user salt); tokens are
  HMAC-SHA256-signed JWT-shaped claims (`sub`/`iat`/`exp`). Both use the standard
  library only — no `passlib`/`pyjwt` dependency.
- `IT_SECRET_KEY` signs tokens; `IT_JWT_TTL_SECONDS` sets lifetime (default 86400).
- Seeding creates a demo admin. If its password is ever missing, regenerate it:
  `python scripts/set_admin_password.py`.

## Admin & user management

The platform has two roles: `admin` and `user`. Admin-only endpoints are
guarded by a `require_admin` dependency (HTTP 403 otherwise).

- **Users** (`/users`, admin): list, create, update (role/status/name), delete,
  and admin reset-password. The last active admin can never be demoted or
  deleted, so the console can't be locked out.
- **Invites** (`/invites`, admin): create an invite for a teammate's email; the
  single-use token is returned. The recipient opens `/accept-invite?token=...`
  (no session required) and sets a password to activate the account.
- **Self-service**: any authenticated user can change their own password at
  `POST /auth/change-password`.

## AI model configuration (real LLM activation)

Analyses run through the agentic engine. When an AI model is configured it is
called; otherwise the engine falls back to a deterministic offline heuristic so
the product stays runnable with no external keys.

- Configure OpenAI-/Anthropic-compatible endpoints at
  `POST/PUT/DELETE /settings/ai-models` (admin). Exactly one config is the
  `default` per scope (`global` or `application`); the engine resolves
  application → global.
- **Secrets stay out of the database.** `api_key_ref` supports `env://NAME`
  (the real key is read from the environment at request time) or a literal key.
  The API never echoes the value back — only `has_key` (boolean).
- Example: create a global default backed by an env var, then run an analysis:

```bash
curl -X POST http://localhost:8000/settings/ai-models \
  -H 'Authorization: Bearer <admin-token>' -H 'Content-Type: application/json' \
  -d '{"scope":"global","provider":"openai","base_url":"https://api.openai.com/v1",
       "api_key_ref":"env://OPENAI_API_KEY","model":"gpt-4o-mini","is_default":true}'
```

## Ingestion → analysis chain

The Kafka consumer (`make consume`) validates each v1.1 alert, persists the
`Alert` + a pending `Analysis`, then triggers `run_analysis` in a background
task so the full `receive → git_sync → context → ai_analysis → memory →
conclusion` workflow runs without blocking ingestion. A failed run is marked
`failed` rather than crashing the consumer.

## Running the full stack (Docker)

```bash
make up        # build + run postgres, kafka, api (:8000), web (:3000)
make seed      # populate demo data into the running api container
make down      # stop (keeps volumes)
make test      # run the pytest suite
```

Locally without Docker:

```bash
make install    # pip install -e ".[dev]"
make dev-up     # postgres + kafka
make serve      # uvicorn (also migrates on boot)
make consume    # kafka consumer
python scripts/seed.py
```

## Tests

```bash
make test        # or: pytest -q
```

Covers password/token security, the auth boundary (401 without token, login+token
flow), and the analysis engine (completion + shared-memory upsert with no duplicate
on re-analysis). The engine test runs against the configured database and cleans up
after itself.

## Kafka contract (spec v1.1)

The message body may contain **only** what the business `lark-alert.ts` tool can
produce. Required: `schema_version` ("1.1"), `level`, `title`, `env`, `timestamp`.
Optional: `eventType`, `project`, `fields`.

Routing is **purely topic-based**: the topic `alert.{product}` maps to an
`application` via `application_kafka`. The platform recomputes `dedupeKey` with the
exact `lark-alert.ts` algorithm so `/analysis/{dedupeKey}` matches the `Key` shown
in the Lark card. Invalid messages go to the dead-letter topic; unmapped topics go
to the unassigned topic.

See `Kafka告警消息格式规范.md` (design repo) for the full contract.
