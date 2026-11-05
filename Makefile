# Developer shortcuts. See README.md.
-include .env
export

COMPOSE := docker compose
RUN := $(COMPOSE) run --rm app

.PHONY: up down db-bootstrap migrate seed test lint race destroy

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

db-bootstrap:
	$(COMPOSE) exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 \
		-v owner_password="$(ROOMLY_OWNER_PASSWORD)" -v app_password="$(ROOMLY_APP_PASSWORD)" \
		-f - < db/000_bootstrap.sql

migrate:
	$(RUN) python -m roomly.migrate

seed:
	$(RUN) python -m roomly.seed

test:
	$(RUN) python -m pytest -q

lint:
	$(RUN) sh -c "ruff check . && ruff format --check ."

race:
	$(RUN) python /app/scripts/race.py --base-url http://app:8000 --n 50

destroy:
	terraform -chdir=infra destroy
