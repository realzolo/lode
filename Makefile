# Lode — developer tasks
# This is a uv-managed project (see uv.lock). Targets below run through `uv run`,
# which uses the project .venv created by `make install` (uv sync). `make` itself
# does NOT read .env — only the Python app does via pydantic-settings.

.PHONY: install migrate serve consume work dev-up dev-down verify test contracts schema-check intake-check resource-check evidence-access-check log-connectors-check native-connectors-check investigation-check analysis-check api-check web-check hardening-check local-release-check provider-release-check

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
	npm ci --prefix tools/logql_parser --ignore-scripts --no-audit --no-fund

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

# Validate and fingerprint the frozen current contracts and release-test corpus.
contracts:
	$(UV) run python scripts/check_contracts.py

# Verify an already-migrated PostgreSQL database against the current invariant contract.
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

# Run fixed parser/policy/provider request-response contract tests.
log-connectors-check:
	$(UV) run pytest -q tests/unit/test_log_evidence_policies.py tests/unit/test_log_evidence_connectors.py

# Run the complete native parser/connector/isolated-runner security contract suite.
native-connectors-check:
	$(UV) run pytest -q tests/unit/test_log_evidence_policies.py tests/unit/test_log_evidence_connectors.py tests/unit/test_sql_evidence_policy.py tests/unit/test_sql_evidence_connectors.py tests/unit/test_https_evidence.py tests/unit/test_command_evidence_policy.py tests/unit/test_command_runner.py

# Exercise connector snapshots, durable waves, Evidence Graph, and lease recovery.
investigation-check:
	$(UV) run pytest -q tests/unit/test_decision_policy.py tests/unit/test_evidence_graph.py tests/unit/test_investigation_orchestration.py
	$(UV) run python scripts/check_investigation_orchestration.py

# Exercise frozen multi-model routing, exact context, replay, role isolation, and drift failure.
analysis-check:
	$(UV) run pytest -q tests/unit/test_model_routing.py tests/unit/test_context_manager.py tests/unit/test_context_compaction.py tests/unit/test_conclusion_authority.py tests/unit/test_model_planner.py tests/unit/test_git_source.py tests/evals/test_analysis_quality.py
	$(UV) run python scripts/check_analysis_quality.py
	$(UV) run python scripts/check_analysis_execution.py

# Verify the frozen API surface, control-plane permissions, secret redaction, and SSE lifecycle.
api-check:
	$(UV) run pytest -q tests/contract/test_api_surface.py tests/unit/test_control_api_schemas.py tests/unit/test_provider_introspection.py tests/test_control_plane_api.py tests/test_investigation_api.py

# Type-check and produce the deployable Workbench build.
web-check:
	npm run typecheck --prefix apps/web
	npm run build --prefix apps/web

# Exercise release evaluation, adversarial security, worker bounds, and lease-loss recovery.
hardening-check:
	$(UV) run pytest -q tests/evals tests/security tests/performance tests/unit/test_evidence_access_kernel.py tests/unit/test_command_runner.py tests/unit/test_metrics_contract.py

# Run every deterministic local release gate. Use a fresh isolated upgraded database.
local-release-check:
	$(MAKE) contracts
	$(MAKE) schema-check
	$(MAKE) intake-check
	$(MAKE) resource-check
	$(MAKE) evidence-access-check
	$(MAKE) log-connectors-check
	$(MAKE) native-connectors-check
	$(MAKE) investigation-check
	$(MAKE) analysis-check
	$(MAKE) api-check
	$(MAKE) hardening-check
	$(UV) run python -m compileall -q src scripts alembic tests
	$(UV) run pytest -q
	$(UV) run python scripts/check_forbidden_contracts.py
	$(MAKE) web-check

# Enforce the statistical AI gate over repeated frozen real-provider observations.
provider-release-check:
	@test -n "$(PROVIDER_OBSERVATIONS)" || (echo "PROVIDER_OBSERVATIONS is required" >&2; exit 2)
	@test -n "$(PROVIDER_RUN_MANIFEST)" || (echo "PROVIDER_RUN_MANIFEST is required" >&2; exit 2)
	@test -n "$(OPERATIONAL_OBSERVATIONS)" || (echo "OPERATIONAL_OBSERVATIONS is required" >&2; exit 2)
	@test -n "$(OPERATIONAL_BASELINE)" || (echo "OPERATIONAL_BASELINE is required" >&2; exit 2)
	@test -n "$(CANARY_BASELINE_OBSERVATIONS)" || (echo "CANARY_BASELINE_OBSERVATIONS is required" >&2; exit 2)
	@test -n "$(CANARY_BASELINE_RUN_MANIFEST)" || (echo "CANARY_BASELINE_RUN_MANIFEST is required" >&2; exit 2)
	@test -n "$(RELEASE_BUNDLE)" || (echo "RELEASE_BUNDLE is required" >&2; exit 2)
	$(UV) run python scripts/check_analysis_quality.py --release --observations "$(PROVIDER_OBSERVATIONS)" --run-manifest "$(PROVIDER_RUN_MANIFEST)" --operational-observations "$(OPERATIONAL_OBSERVATIONS)" --operational-baseline "$(OPERATIONAL_BASELINE)" --canary-baseline-observations "$(CANARY_BASELINE_OBSERVATIONS)" --canary-baseline-run-manifest "$(CANARY_BASELINE_RUN_MANIFEST)" --release-bundle "$(RELEASE_BUNDLE)"

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
