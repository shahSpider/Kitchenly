.PHONY: install db test lint migrate run worker beat demo up down
PY=.venv/bin/python
install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements/dev.txt
db:       ## throwaway Postgres on :55432 with the non-superuser app role
	docker run -d --name kitchenly-pg -e POSTGRES_PASSWORD=postgres -e APP_DB_PASSWORD=kitchenly -p 127.0.0.1:55432:5432 \
	  -v $(PWD)/docker/postgres/init.sh:/docker-entrypoint-initdb.d/init.sh:ro postgres:17-alpine
test:
	$(PY) -m pytest -q
lint:
	.venv/bin/ruff check .
migrate:
	$(PY) manage.py migrate
run:
	$(PY) manage.py runserver
worker:
	.venv/bin/celery -A config worker -l info -Q webhook,pull,import,push,default
beat:
	.venv/bin/celery -A config beat -l info
demo:
	$(PY) manage.py seed_demo
up:
	docker compose up -d --build
down:
	docker compose down
