.PHONY: setup up down migrate test lint format worker api chatbot

setup:
	poetry install --with dev

setup-search:
	poetry install --with dev,search

setup-worker:
	poetry install --with dev,search,worker

setup-chatbot:
	poetry install --with dev,search,chatbot

setup-all:
	poetry install --with dev,search,worker,chatbot

up:
	docker compose up -d

down:
	docker compose down

migrate:
	cd migrations && poetry run alembic upgrade head

test:
	poetry run pytest tests/ -v

lint:
	poetry run ruff check src/

format:
	poetry run ruff format src/

api:
	poetry run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	poetry run celery -A worker.app worker --loglevel=info -Q gpu.high,gpu.medium,db -c 4

chatbot:
	poetry run uvicorn chatbot.main:app --host 0.0.0.0 --port 8001 --reload
