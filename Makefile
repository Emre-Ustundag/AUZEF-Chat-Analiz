# AUZEF Chat Analiz — geliştirme komutları (ADR-0001 §3).
#
# Backend komutları uv, frontend komutları npm workspace kullanır.

BACKEND := apps/backend
UV := uv run --project $(BACKEND)

.DEFAULT_GOAL := help
.PHONY: help install dev lint format typecheck test build contract openapi fixtures generate check clean

help: ## Komutları listele
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Node ve Python bağımlılıklarını kur
	npm ci
	cd $(BACKEND) && uv sync --dev

dev: ## Frontend geliştirme sunucusu
	npm run dev

lint: ## Tüm lint kapıları
	npm run lint
	npm run format:check
	cd $(BACKEND) && uv run ruff check .
	cd $(BACKEND) && uv run ruff format --check .

format: ## Biçimlendir
	npm run format
	cd $(BACKEND) && uv run ruff format .

typecheck: ## TypeScript + mypy
	npm run typecheck
	cd $(BACKEND) && uv run mypy

test: ## Tüm testler
	npm test
	cd $(BACKEND) && uv run pytest

build: ## Frontend production build
	NEXT_TELEMETRY_DISABLED=1 npm run build

openapi: ## docs/api/openapi.json üret
	$(UV) python $(BACKEND)/scripts/export_openapi.py

fixtures: ## tests/fixtures/contract/ üret
	$(UV) python $(BACKEND)/scripts/export_fixtures.py

generate: openapi fixtures ## Tüm sözleşme artefaktlarını yeniden üret

contract: ## Sözleşme drift kontrolü (CI'ın çalıştırdığı)
	$(UV) python $(BACKEND)/scripts/export_openapi.py --check
	$(UV) python $(BACKEND)/scripts/export_fixtures.py --check
	git diff --exit-code -- docs/api/openapi.json tests/fixtures/contract
	npm run test:contract

check: lint typecheck test contract build ## CI'ın tamamı

clean: ## Üretilmiş çıktıları temizle
	rm -rf apps/web/.next apps/web/coverage
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	find $(BACKEND) -type d -name __pycache__ -prune -exec rm -rf {} +
