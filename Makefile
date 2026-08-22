# Lode — developer tasks
# Always run inside the project virtualenv (see README).

.PHONY: install migrate serve consume dev-up dev-down verify test

# Defaults for the `serve` target. Override from the shell if needed — make does
# NOT read .env (only the Python app does, via pydantic-settings), so without
# these the recipe expands to an empty --host/--port and uvicorn fails to bind.
LODE_HTTP_HOST ?= 127.0.0.1
LODE_HTTP_PORT ?= 8000

install:
	pip install -e ".[dev]"

# Auto-execute database migrations (Alembic) against LODE_DATABASE_URL.
migrate:
	alembic upgrade head

# Run the API server. Migrations run automatically on startup (lifespan hook).
serve:
	uvicorn lode.api.main:app --host $(LODE_HTTP_HOST) --port $(LODE_HTTP_PORT) --reload

# Run the Kafka consumer.
consume:
	python -m lode.consumer.main

# Start Postgres + Kafka via docker-compose.
dev-up:
	docker compose up -d

dev-down:
	docker compose down

# Start a local Postgres, run migrations, then print the schema summary.
verify:
	./scripts/verify.sh

test:
	pytest -q

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
