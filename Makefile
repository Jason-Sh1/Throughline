# Throughline task runner. Run `make help` for the list.
SHELL := /bin/bash
WORKER := cd worker && uv run
PG_TEST_URL ?= postgresql://postgres:postgres@localhost:54322/throughline_test

.PHONY: help setup dev test test-web test-worker lint ingest eval-quick validate-feeds \
        sync-sources allsides db-test

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Install web and worker dependencies
	cd web && npm ci
	cd worker && uv sync

dev: ## Run the web app locally
	cd web && npm run dev

test: test-worker test-web ## Run all tests and checks

test-worker: ## Lint + test the Python worker
	$(WORKER) ruff check .
	$(WORKER) ruff format --check .
	$(WORKER) pytest -q

test-web: ## Lint + typecheck the web app
	cd web && npm run lint && npm run typecheck

lint: ## Lint only
	$(WORKER) ruff check .
	cd web && npm run lint

validate-feeds: ## Validate feeds/feeds.yaml (STRICT=1 enforces launch coverage targets)
	$(WORKER) python -m ingest.feeds_config $(if $(STRICT),--strict,)

sync-sources: validate-feeds ## Load feeds.yaml into the sources/feeds tables
	$(WORKER) python -m ingest.sync_sources

allsides: ## Import AllSides ratings: make allsides CSV=path/to.csv [APPLY=1]
	@test -n "$(CSV)" || (echo "usage: make allsides CSV=path/to/allsides.csv [APPLY=1]" && exit 1)
	$(WORKER) python -m ingest.allsides $(abspath $(CSV)) $(if $(APPLY),--apply,)

ingest: sync-sources ## Fetch feeds and cluster (fetchers arrive in M2)
	@echo "Fetchers and clustering are not implemented yet (ROADMAP M2)."

eval-quick: ## 30-story eval gate (arrives in M4)
	@echo "No eval suite yet (ROADMAP M4); passing."

db-test: ## Apply migrations to PG_TEST_URL and run RLS + DB tests
	psql "$(PG_TEST_URL)" -v ON_ERROR_STOP=1 -q -f supabase/tests/local_auth_shim.sql
	for f in supabase/migrations/*.sql; do psql "$(PG_TEST_URL)" -v ON_ERROR_STOP=1 -q -f $$f; done
	psql "$(PG_TEST_URL)" -q -f supabase/tests/rls_test.sql
	cd worker && TEST_DATABASE_URL="$(PG_TEST_URL)" uv run pytest -q tests/test_sync_sources.py
