# Entry point for every routine operation. A target must never require a manual
# step before or after it; if something is not wired up yet, the target says so
# and names the plan task that will wire it.

SHELL := /bin/bash
.DEFAULT_GOAL := help

SERVICES     := identity catalog booking payment notification
COMPOSE_FILE := deploy/local/docker-compose.yml
COMPOSE      := docker compose -f $(COMPOSE_FILE)
UV           := uv run

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# --- environment -----------------------------------------------------------

.PHONY: sync
sync: ## Install the workspace environment from uv.lock
	uv sync --locked

.PHONY: hooks
hooks: sync ## Install git hooks (pre-commit and commit-msg)
	$(UV) pre-commit install --install-hooks

# --- quality ---------------------------------------------------------------

.PHONY: lint
lint: ## Run ruff lint and format checks
	$(UV) ruff check .
	$(UV) ruff format --check .

.PHONY: format
format: ## Apply ruff fixes and formatting
	$(UV) ruff check --fix .
	$(UV) ruff format .

.PHONY: typecheck
typecheck: ## Run mypy in strict mode
	$(UV) mypy

.PHONY: test
test: ## Run the test suite
	$(UV) pytest

.PHONY: ci
ci: lint typecheck test ## Run every check the pipeline runs

# --- local infrastructure --------------------------------------------------

.PHONY: up
up: ## Start the local infrastructure stack and wait until it is ready
	@if [[ ! -f "$(COMPOSE_FILE)" ]]; then \
		echo "skip: $(COMPOSE_FILE) does not exist yet; it arrives with P0-T06."; \
	else \
		$(COMPOSE) up --detach --wait; \
	fi

.PHONY: down
down: ## Stop the local infrastructure stack
	@if [[ ! -f "$(COMPOSE_FILE)" ]]; then \
		echo "skip: $(COMPOSE_FILE) does not exist yet; it arrives with P0-T06."; \
	else \
		$(COMPOSE) down --remove-orphans; \
	fi

# --- databases -------------------------------------------------------------

# Migrations run per service against that service's own database: there is one
# Alembic history per service and no cross-database access (D19, D42).
.PHONY: migrate
migrate: ## Apply migrations for every service that has them
	@applied=0; \
	for service in $(SERVICES); do \
		if [[ -d "services/$$service/migrations" ]]; then \
			applied=1; \
			echo "==> $$service"; \
			(cd "services/$$service" && $(UV) alembic upgrade head) || exit 1; \
		fi; \
	done; \
	if [[ $$applied -eq 0 ]]; then \
		echo "skip: no service has migrations yet; the first arrives with P1-T08."; \
	fi

.PHONY: seed
seed: ## Seed reference data for every service that exposes a seed command
	@seeded=0; \
	for service in $(SERVICES); do \
		if [[ -d "services/$$service/src/$$service/cli" ]]; then \
			seeded=1; \
			echo "==> $$service"; \
			$(UV) python -m "$$service.cli" seed || exit 1; \
		fi; \
	done; \
	if [[ $$seeded -eq 0 ]]; then \
		echo "skip: no service exposes a seed command yet; the first arrives with P1-T10."; \
	fi
