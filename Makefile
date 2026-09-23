SHELL := /bin/sh
.DEFAULT_GOAL := help

COMPOSE ?= docker compose

.PHONY: help up down logs ps deploy

help:
	@printf 'make up      Build and start the full project locally\n'
	@printf 'make down    Stop the local project (data volumes are kept)\n'
	@printf 'make logs    Follow all service logs\n'
	@printf 'make ps      Show container status\n'
	@printf 'make deploy  Pull updates and start the full project on this server\n'

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

deploy:
	@test -f .env || { printf '%s\n' 'Missing .env on this server. Copy .env.example to .env and set V2V_API_KEY and POSTGRES_PASSWORD. ROUTER_ADMIN_TOKEN is optional for admin editing.' >&2; exit 1; }
	git pull --ff-only
	$(COMPOSE) up --build -d
