SHELL := /bin/sh
.DEFAULT_GOAL := help

COMPOSE ?= docker compose

.PHONY: help up down logs ps deploy validate-official import-official

help:
	@printf 'make up      Build and start the full project locally\n'
	@printf 'make down    Stop the local project (data volumes are kept)\n'
	@printf 'make logs    Follow all service logs\n'
	@printf 'make ps      Show container status\n'
	@printf 'make deploy  Pull updates and start the full project on this server\n'
	@printf 'make validate-official  Validate the official starter kit without replacing data\n'
	@printf 'make import-official    Replace the active catalogue with the official starter kit\n'

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

validate-official:
	$(COMPOSE) run --rm -v "$(CURDIR)/case_2/voice_router_dataset:/starter-kit:ro" backend python -m app.import_catalog /starter-kit

# Explicit administrator operation: replaces the live catalogue/reference data.
import-official:
	$(COMPOSE) run --rm -v "$(CURDIR)/case_2/voice_router_dataset:/starter-kit:ro" backend python -m app.import_catalog /starter-kit --replace

deploy:
	@test -f .env || { printf '%s\n' 'Missing .env on this server. Copy .env.example to .env and set V2V_API_KEY and POSTGRES_PASSWORD. ROUTER_ADMIN_TOKEN is optional for admin editing.' >&2; exit 1; }
	git pull --ff-only
	$(COMPOSE) up --build -d
