# Entry point for every routine operation. A target must never require a manual
# step before or after it; if something is not wired up yet, the target says so
# and names the plan task that will wire it.

SHELL := /bin/bash
.DEFAULT_GOAL := help

SERVICES     := identity catalog booking payment notification
OVERLAYS     := dev prod
COMPOSE_FILE := deploy/local/docker-compose.yml
COMPOSE      := docker compose -f $(COMPOSE_FILE)
UV           := uv run
CLUSTER_NAME := booking
KUBE_CONTEXT := kind-$(CLUSTER_NAME)
KUBECTL      := kubectl --context $(KUBE_CONTEXT)
AGE_KEY_FILE := $(HOME)/.config/sops/age/keys.txt

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

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

# Containers first, then the state they must hold: a broker with no topics
# passes its healthcheck and fails the first consumer. The target returns only
# once every component answers a real readiness probe.
.PHONY: up
up: ## Start the local infrastructure stack and wait until it is ready
	$(COMPOSE) up --detach --wait
	@echo
	@echo "==> buckets"
	@$(COMPOSE) run --rm --quiet-pull minio-init
	@echo
	@echo "==> topics"
	@scripts/create_topics.sh apply
	@echo
	@scripts/wait_for_stack.sh

.PHONY: down
down: ## Stop the local infrastructure stack
	$(COMPOSE) down --remove-orphans

.PHONY: ready
ready: ## Check that every component of the running stack is ready
	@scripts/wait_for_stack.sh

.PHONY: topics
topics: ## Create the Kafka topics and align their configuration
	@scripts/create_topics.sh apply

# --- cluster ---------------------------------------------------------------

.PHONY: tools
tools: ## Install the pinned cluster toolchain into ~/.local/bin
	@scripts/install_tools.sh

.PHONY: cluster-up
cluster-up: ## Create the kind cluster with an Ingress controller
	@scripts/cluster_up.sh

.PHONY: cluster-down
cluster-down: ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER_NAME)

.PHONY: manifests
manifests: ## Build every overlay
	@for overlay in $(OVERLAYS); do \
		echo "==> $$overlay"; \
		kustomize build "deploy/k8s/overlays/$$overlay" || exit 1; \
	done

# --- gitops ----------------------------------------------------------------

.PHONY: argocd-up
argocd-up: ## Install ArgoCD and apply the root application
	@scripts/argocd_up.sh

.PHONY: argocd-ui
argocd-ui: ## Forward the ArgoCD UI to http://localhost:8080
	$(KUBECTL) --namespace argocd port-forward service/argocd-server 8080:80

.PHONY: argocd-password
argocd-password: ## Print the initial ArgoCD admin password
	@$(KUBECTL) --namespace argocd get secret argocd-initial-admin-secret \
		--output jsonpath='{.data.password}' | base64 --decode; echo

# --- secrets ---------------------------------------------------------------

# The private key lives outside the repository and is the one thing here that
# cannot be recreated from it (D52). See docs/RUNBOOK.md.
.PHONY: secrets-key
secrets-key: ## Generate the age key if it does not exist
	@if [[ -f "$(AGE_KEY_FILE)" ]]; then \
		echo "age key already exists at $(AGE_KEY_FILE)"; \
	else \
		mkdir -p "$$(dirname "$(AGE_KEY_FILE)")"; \
		age-keygen --output "$(AGE_KEY_FILE)" >/dev/null 2>&1; \
		chmod 600 "$(AGE_KEY_FILE)"; \
		echo "created $(AGE_KEY_FILE); put the public key below into .sops.yaml"; \
	fi
	@echo "public key: $$(age-keygen -y "$(AGE_KEY_FILE)")"

.PHONY: secrets-push
secrets-push: ## Load the age key into the cluster for ArgoCD to decrypt with
	@$(KUBECTL) --namespace argocd create secret generic sops-age \
		--from-file="keys.txt=$(AGE_KEY_FILE)" \
		--dry-run=client --output=yaml | $(KUBECTL) apply --filename -

.PHONY: secrets-edit
secrets-edit: ## Decrypt, edit and re-encrypt: make secrets-edit FILE=path
	@if [[ -z "$(FILE)" ]]; then \
		echo "usage: make secrets-edit FILE=deploy/k8s/base/secrets/<name>.enc.yaml"; \
		exit 2; \
	fi
	sops "$(FILE)"

.PHONY: secrets-build
secrets-build: ## Render the encrypted secrets locally (needs the age key)
	@kustomize build --enable-alpha-plugins --enable-exec deploy/k8s/base/secrets

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
