# Lode — developer tasks
# This is a uv-managed project (see uv.lock). Targets below run through `uv run`,
# which uses the project .venv created by `make install` (uv sync). `make` itself
# does NOT read .env — only the Python app does via pydantic-settings.

.PHONY: install migrate serve consume dev-up dev-down verify test contracts schema-check intake-check resource-check evidence-access-check

# uv binary to use. Override from the shell if it is not on PATH, e.g.
#   make serve UV=/Users/lixm/.local/bin/uv
UV ?= uv

# Defaults for the `serve` target. Override from the shell if needed — make does
# NOT read .env (only the Python app does, via pydantic-settings), so without
# these the recipe expands to an empty --host/--port and uvicorn fails to bind.
LODE_HTTP_HOST ?= 127.0.0.1
LODE_HTTP_PORT ?= 8000

# Install deps into the project .venv from the lockfile (incl. dev + pgvector).
install:
	$(UV) sync --all-extras

# Auto-execute database migrations (Alembic) against LODE_DATABASE_URL.
migrate:
	$(UV) run alembic upgrade head

# Run the API server. Migrations run automatically on startup (lifespan hook).
serve:
	$(UV) run uvicorn lode.api.main:app --host $(LODE_HTTP_HOST) --port $(LODE_HTTP_PORT) --reload

# Run the Kafka consumer.
consume:
	$(UV) run python -m lode.consumer.main

# Run the durable analysis worker (claims + executes queued jobs).
work:
	$(UV) run python -m lode.worker.main

# Start Postgres + Kafka via docker-compose.
dev-up:
	docker compose up -d

dev-down:
	docker compose down

# Start a local Postgres, run migrations, then print the schema summary.
verify:
	./scripts/verify.sh

test:
	$(UV) run pytest -q

# Validate and fingerprint the frozen V1 contracts and release-test corpus.
contracts:
	$(UV) run python scripts/check_contracts.py

# Verify an already-migrated PostgreSQL database against the V1 invariant contract.
schema-check:
	$(UV) run python scripts/check_schema.py
	$(UV) run python scripts/check_database_behavior.py

# Exercise Kafka/manual intake, idempotency races, DLQ, replay, and ValueRef storage.
intake-check:
	$(UV) run python scripts/check_intake.py

# Exercise repository discovery, deterministic validation, publication, and snapshots.
resource-check:
	$(UV) run python scripts/check_resource_graph.py

# Exercise native-read policy, ValueRef binding, immutable audit, and replay defense.
evidence-access-check:
	$(UV) run python scripts/check_evidence_access.py

# Build and run the full stack (postgres, kafka, api, web) via Docker.
up:
	docker compose up -d --build

# Stop the full stack (keeps volumes).
down:
	docker compose down

# Seed demo data into the running api container's database.
seed:
	docker compose exec app python scripts/seed.py

# Tail logs for the api container.
logs:
	docker compose logs -f app
